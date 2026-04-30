"""Dashboard MTProto login for `/gc` Telethon sessions (SMS code + optional 2FA)."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from telethon.errors import PhoneCodeInvalidError, SessionPasswordNeededError
from telethon.errors.rpcerrorlist import PhoneCodeExpiredError, PhoneNumberInvalidError

from api.auth import get_current_admin
from api.schemas import (
    GcMtProtoClubRead,
    MtProtoPasswordRequest,
    MtProtoSendCodeRequest,
    MtProtoSendCodeResponse,
    MtProtoSignInRequest,
    MtProtoSignInResponse,
)
from bot.services.gc_phone import PHONE_INVALID_REPLY, normalize_phone_for_mtproto, phone_len_bounds_ok
from bot.services.mtproto_group_create import (
    authenticate_mtproto_code,
    authenticate_mtproto_password,
    is_client_authorized,
    send_code_for_phone,
)
from club_gc_settings import CLUB_GC_CONFIG, ClubGcConfig, get_tg_mtproto_credentials

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/gc/mtproto",
    tags=["gc-mtproto"],
    dependencies=[Depends(get_current_admin)],
)


def _cfg(club_key: str) -> ClubGcConfig:
    c = CLUB_GC_CONFIG.get(club_key)
    if c is None:
        raise HTTPException(status_code=404, detail=f"Unknown club_key: {club_key!r}")
    return c


async def _require_api_credentials() -> None:
    try:
        get_tg_mtproto_credentials()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.get("/clubs", response_model=list[GcMtProtoClubRead])
async def list_gc_mtproto_clubs():
    await _require_api_credentials()
    configs = list(CLUB_GC_CONFIG.values())
    flags = await asyncio.gather(*(is_client_authorized(c) for c in configs))
    return [
        GcMtProtoClubRead(
            club_key=c.club_key,
            club_display_name=c.club_display_name,
            session_authorized=a,
            phone_configured=c.mtproto_phone_number is not None,
        )
        for c, a in zip(configs, flags)
    ]


def _resolve_phone(cfg: ClubGcConfig, body_phone: str | None) -> str:
    raw = cfg.mtproto_phone_number.strip() if cfg.mtproto_phone_number else ""
    if raw:
        src = raw
    elif body_phone and body_phone.strip():
        src = body_phone.strip()
    else:
        raise HTTPException(
            status_code=400,
            detail=(
                "No phone on file for this club — submit `phone` (+country…) or set MT_PROTO_PHONE_* on the server."
            ),
        )
    plus = normalize_phone_for_mtproto(src)
    if not phone_len_bounds_ok(plus):
        raise HTTPException(status_code=400, detail=PHONE_INVALID_REPLY)
    return plus


@router.post("/send-code", response_model=MtProtoSendCodeResponse)
async def mtproto_send_code(body: MtProtoSendCodeRequest):
    """Request Telegram login code. Returns ``phone_code_hash`` for the follow-up ``sign-in`` call."""
    await _require_api_credentials()
    cfg = _cfg(body.club_key)
    phone = _resolve_phone(cfg, body.phone)
    try:

        phone_code_hash = await send_code_for_phone(cfg, phone)
    except PhoneNumberInvalidError:
        logger.warning("MTProto SendCode invalid phone club=%s", cfg.club_key)
        raise HTTPException(status_code=400, detail=PHONE_INVALID_REPLY) from None
    except RuntimeError as e:
        raise HTTPException(status_code=429, detail=str(e)) from e
    except Exception:
        logger.exception("mtproto_send_code")
        raise HTTPException(status_code=500, detail="SendCode failed (see server logs).") from None

    return MtProtoSendCodeResponse(
        ok=True,
        message="Enter the Telegram/SMS login code below (one attempt per code — request a new one if needed).",
        phone_code_hash=phone_code_hash,
        phone_e164=phone,
    )


@router.post("/sign-in", response_model=MtProtoSignInResponse)
async def mtproto_sign_in(body: MtProtoSignInRequest):
    await _require_api_credentials()
    cfg = _cfg(body.club_key)

    plus = normalize_phone_for_mtproto(body.phone.strip())
    if not phone_len_bounds_ok(plus):
        raise HTTPException(status_code=400, detail=PHONE_INVALID_REPLY)

    code = "".join(c for c in body.code if c.isdigit()) or body.code.strip()
    if len(code) < 3:
        raise HTTPException(status_code=400, detail="Code looks too short.")

    h = body.phone_code_hash.strip()
    if len(h) < 8:
        raise HTTPException(status_code=400, detail="Missing or invalid phone_code_hash — run send-code again.")

    try:
        await authenticate_mtproto_code(
            cfg,
            phone=plus,
            code=code,
            phone_code_hash=h,
        )

    except SessionPasswordNeededError:
        return MtProtoSignInResponse(logged_in=False, needs_password=True)

    except PhoneCodeExpiredError as e:
        logger.warning("PhoneCodeExpired club=%s %s", cfg.club_key, e)
        raise HTTPException(
            status_code=400,
            detail="That confirmation code has expired or was invalidated. Request a new code.",
        ) from e

    except PhoneCodeInvalidError:
        raise HTTPException(
            status_code=400,
            detail="Invalid login code. Request a new code from Telegram and try again.",
        ) from None

    except Exception as e:
        logger.warning("MTProto sign-in failure %s", type(e).__name__)
        raise HTTPException(status_code=400, detail=str(e) or type(e).__name__) from e

    return MtProtoSignInResponse(logged_in=True, needs_password=False)


@router.post("/cloud-password", response_model=MtProtoSignInResponse)
async def mtproto_cloud_password(body: MtProtoPasswordRequest):
    await _require_api_credentials()
    cfg = _cfg(body.club_key)
    pwd = body.password.strip()
    if not pwd:
        raise HTTPException(status_code=400, detail="Password is required.")

    try:
        await authenticate_mtproto_password(cfg, password=pwd)
    except Exception as e:
        logger.warning("MTProto cloud password failure %s", type(e).__name__)
        raise HTTPException(status_code=400, detail=str(e) or "Cloud Password not accepted.") from e

    return MtProtoSignInResponse(logged_in=True, needs_password=False)
