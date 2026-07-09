"""Deposit conversation: amount first, then filtered method selection, then optional crypto sub-option."""

import html
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationHandlerStop,
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from config import ADMIN_USER_IDS
from bot.services.club import (
    get_club_for_chat,
    get_methods_for_amount,
    get_method_by_id,
    get_sub_options,
    get_sub_option_by_id,
    get_club_allows_admin_commands,
    get_club_simple_mode,
    get_tier_for_amount,
    get_lowest_minimum,
    record_activity,
    pick_variant,
    is_first_deposit,
    is_first_deposit_claimed,
    get_first_deposit_settings,
    update_group_name,
    record_method_deposit,
    get_deposit_method_names,
    is_club_staff,
    set_last_deposit_union,
)
from bot.services.mtproto_group_rename import rename_support_group_title
from bot.services.player_details import merge_union_prefix
from bot.services.round_table_unions import (
    ROUND_TABLE_DEPOSIT_UNIONS,
    is_round_table_club,
    union_label_for_shorthand,
)
from bot.handlers.flow_cancel import clear_active_flow, mark_active_flow
from bot.handlers.flow_staleness import (
    AMOUNT_TEXT,
    deposit_amount_actor_allowed,
    deposit_amount_show_validation_error,
    handle_stale_flow_callback,
    is_update_too_old,
    log_stale_update,
    looks_like_amount,
    register_flow_callback_message,
    reset_flow_callback_messages,
)
from bot.handlers.response_utils import send_response_messages
from bot.services.stripe_deposit import (
    create_stripe_checkout_session,
    stripe_configured,
)
from bot.services.payment_method_binding import (
    BIND_ATTEMPT_TTL_SECONDS,
    BIND_KIND_MEMO_EMOJI,
    BIND_KIND_SPECIAL_AMOUNT,
    bind_mode_for_method,
    format_first_time_memo_instructions_message,
    format_first_time_payment_destination_message,
    format_first_time_special_amount_setup_message,
    get_chat_binding,
    is_chat_method_bound,
    get_pending_bind_attempt,
    deposit_amount_to_cents,
    start_bind_attempt,
)
from bot.services.payment_method_binding import expire_attempt as expire_bind_attempt
from bot.services.deposit_funnel_events import (
    STEP_AMOUNT_ENTERED,
    STEP_BIND_SETUP_COMPLETED,
    STEP_DEPOSIT_STARTED,
    STEP_INSTRUCTIONS_SENT,
    STEP_METHOD_CHOSEN,
    STEP_REFERRAL_COMPLETED,
    STEP_UNION_CHOSEN,
    new_deposit_session_id,
    record_deposit_funnel_event,
)
from bot.runtime_config import is_test_bot_worker, use_payment_v2
from db.connection import get_db
from db.models import (
    CashAppPayment,
    Club,
    CryptoPayment,
    PayPalPayment,
    PlayerActivity,
    StripeCheckoutSession,
    VenmoPayment,
    ZellePayment,
)

logger = logging.getLogger(__name__)

DEPOSIT_REFERRAL, DEPOSIT_AMOUNT, DEPOSIT_UNION, DEPOSIT_CHOOSE, DEPOSIT_SUB, DEPOSIT_SETUP_ACK = range(6)


def _ensure_deposit_session(context: ContextTypes.DEFAULT_TYPE) -> str:
    session_id = context.chat_data.get("deposit_session_id")
    if not session_id:
        session_id = new_deposit_session_id()
        context.chat_data["deposit_session_id"] = session_id
    return str(session_id)


def _funnel_amount_cents(context: ContextTypes.DEFAULT_TYPE) -> int | None:
    amount = context.chat_data.get("deposit_amount")
    if not isinstance(amount, Decimal):
        return None
    try:
        return deposit_amount_to_cents(amount)
    except Exception:
        return None


def _record_funnel_from_context(
    context: ContextTypes.DEFAULT_TYPE,
    step: str,
    *,
    method_slug: str | None = None,
    metadata: dict | None = None,
) -> None:
    chat_id = context.chat_data.get("deposit_chat_id")
    if chat_id is None:
        return
    try:
        record_deposit_funnel_event(
            deposit_session_id=_ensure_deposit_session(context),
            step=step,
            telegram_chat_id=int(chat_id),
            club_id=context.chat_data.get("deposit_club_id"),
            telegram_user_id=context.chat_data.get("deposit_user_id"),
            method_slug=method_slug,
            amount_cents=_funnel_amount_cents(context),
            is_first_deposit=bool(context.chat_data.get("deposit_is_first", False)),
            requires_method_setup=bool(
                context.chat_data.get("deposit_requires_method_setup", False)
            ),
            metadata=metadata,
        )
    except Exception:
        logger.debug(
            "deposit funnel record failed step=%s chat_id=%s",
            step,
            chat_id,
            exc_info=True,
        )


# Test-bot fallback when chat_data does not persist between updates (group chats).
_DEPOSIT_AWAITING_CHATS: set[int] = set()

_CHECKOUT_SETTING_KEYS = (
    "use_group_checkout_link",
    "group_checkout_provider",
    "hyperlink_text",
    "checkout_min_amount",
    "checkout_max_amount",
)

_LEGACY_RANDOM_EMOJI_RE = re.compile(
    r"\n?\s*•\s*Please put a random emoji in the payment caption when (?:you )?sending\s*",
    re.IGNORECASE,
)


def _strip_legacy_random_emoji_instruction(
    data: dict,
    method_slug: str,
) -> dict:
    """Remove seeded copy that asked for a random emoji on every deposit."""
    slug = (method_slug or "").strip().lower()
    if slug not in ("venmo", "zelle", "cashapp", "paypal"):
        return data
    out = dict(data)
    changed = False
    for field in ("response_text", "response_caption"):
        text = out.get(field)
        if not text or not isinstance(text, str):
            continue
        cleaned = _LEGACY_RANDOM_EMOJI_RE.sub("\n", text)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        if cleaned != text and cleaned:
            out[field] = cleaned
            changed = True
    return out if changed else data


def _response_data_has_content(data: dict | None) -> bool:
    if not data:
        return False
    rtype = (data.get("response_type") or "text").strip().lower()
    if rtype == "photo":
        if (data.get("response_file_id") or "").strip():
            return True
        return bool((data.get("response_caption") or "").strip())
    return bool((data.get("response_text") or "").strip())


def _merge_response_layers(response_data: dict, *fallback_layers: dict | None) -> dict:
    """Fill missing variant copy from tier/method when v2 stores text on the parent layer."""
    merged = dict(response_data)
    if _response_data_has_content(merged):
        return merged
    for layer in fallback_layers:
        if not layer:
            continue
        candidate = dict(merged)
        for field in ("response_type", "response_text", "response_file_id", "response_caption"):
            value = layer.get(field)
            if value is not None:
                candidate[field] = value
        if _response_data_has_content(candidate):
            return candidate
    return merged


def _normalize_misconfigured_response_type(data: dict) -> dict:
    """Treat text-only rows as text when response_type says photo but file_id is missing."""
    out = dict(data)
    rtype = (out.get("response_type") or "text").strip().lower()
    has_photo = rtype == "photo" and bool((out.get("response_file_id") or "").strip())
    has_text = bool((out.get("response_text") or "").strip())
    if rtype == "photo" and not has_photo and has_text:
        out["response_type"] = "text"
    return out


def _zelle_venmo_destination_fallback(response_data: dict, method_slug: str) -> dict | None:
    slug = (method_slug or "").strip().lower()
    if slug not in ("zelle", "venmo"):
        return None
    raw = (response_data.get("response_text") or response_data.get("response_caption") or "").strip()
    if not raw:
        return None
    text = format_first_time_payment_destination_message(
        payment_method_slug=slug,
        variant_response_text=raw,
        use_html=False,
    )
    if not text.strip():
        return None
    return {"response_type": "text", "response_text": text}


def _prepare_deposit_response_data(
    response_data: dict,
    *,
    method_slug: str,
    method: dict | None = None,
    tier: dict | None = None,
    allow_destination_fallback: bool = True,
) -> dict:
    data = _strip_legacy_random_emoji_instruction(response_data, method_slug)
    data = _merge_response_layers(data, tier, method)
    data = _normalize_misconfigured_response_type(data)
    if not _response_data_has_content(data):
        if allow_destination_fallback:
            fallback = _zelle_venmo_destination_fallback(data, method_slug)
            if fallback:
                data = {**data, **fallback}
    return data


def _apply_checkout_layer(target: dict, source: dict | None) -> None:
    """Overlay checkout fields from source onto target (method → tier → variant).

    Stripe min/max use checkout_min_amount / checkout_max_amount only — never tier
    deposit-band min_amount / max_amount ($101–$2000), which are separate limits.
    """
    if not source:
        return
    if source.get("use_group_checkout_link") is not None:
        enabled = bool(source["use_group_checkout_link"])
        target["use_group_checkout_link"] = enabled
        if not enabled:
            target.pop("group_checkout_provider", None)
    if source.get("group_checkout_provider"):
        target["group_checkout_provider"] = source.get("group_checkout_provider")
    elif target.get("use_group_checkout_link") and not target.get("group_checkout_provider"):
        target["group_checkout_provider"] = "stripe"
    if source.get("hyperlink_text"):
        target["hyperlink_text"] = source.get("hyperlink_text")
    if source.get("checkout_min_amount") is not None:
        target["checkout_min_amount"] = source.get("checkout_min_amount")
    if source.get("checkout_max_amount") is not None:
        target["checkout_max_amount"] = source.get("checkout_max_amount")


def _with_method_checkout_settings(
    response_data: dict,
    method: dict | None,
    *,
    tier: dict | None = None,
) -> dict:
    """Merge checkout settings: method → tier → variant (response_data)."""
    merged = dict(response_data)
    checkout: dict = {}
    for layer in (method, tier, response_data):
        _apply_checkout_layer(checkout, layer)
    merged.update(checkout)

    if merged.get("use_group_checkout_link"):
        if tier:
            if merged.get("checkout_min_amount") is None:
                if tier.get("checkout_min_amount") is not None:
                    merged["checkout_min_amount"] = tier["checkout_min_amount"]
                elif tier.get("min_amount") is not None:
                    merged["checkout_min_amount"] = tier["min_amount"]
            if merged.get("checkout_max_amount") is None:
                if tier.get("checkout_max_amount") is not None:
                    merged["checkout_max_amount"] = tier["checkout_max_amount"]
                elif tier.get("max_amount") is not None:
                    merged["checkout_max_amount"] = tier["max_amount"]
        if merged.get("checkout_max_amount") is None and method:
            if method.get("max_amount") is not None:
                merged["checkout_max_amount"] = method["max_amount"]
        # Open-ended "Over $100" tiers often have no checkout_max in DB; variant enables Stripe only.
        if merged.get("checkout_max_amount") is None:
            lo = merged.get("checkout_min_amount")
            if isinstance(lo, Decimal) and lo >= Decimal("101"):
                merged["checkout_max_amount"] = Decimal("2000")

    return merged


# Temporary until dashboard tier/variant Stripe flags are fully migrated (Apple Pay only).
_STRIPE_HARDCODE_SLUGS = frozenset({"applepay"})
_STRIPE_HARDCODE_MAX = Decimal("100")
_STRIPE_HARDCODE_DEFAULT_TEXT = (
    "🚨 NO CREDIT CARDS. They will be refunded immediately\n\n"
    "• Enter your deposit amount on the checkout page ($20 minimum, $100 maximum).\n\n"
    "• Once sent, please inform us, and an agent will confirm the transaction "
    "and add your chips within 2 minutes!\n\n"
    "• Just post a screenshot of your transaction, and it will be credited to your account!\n\n"
    "{{hyperlink}}"
)
_PLACEHOLDER_RESPONSE_TEXT = frozenset({"long text", "test", "placeholder", "todo"})

DEPOSIT_REMINDER_SECONDS = 600  # 10 minutes
_PAYMENT_RECEIVED_SNIPPET = "we have received your payment"

# Maps chat_id → customer user_id that we're waiting on for a deposit follow-up.
_PENDING_DEPOSIT_REMINDERS: dict[int, int] = {}
# Maps chat_id → deposit instruction message ids to delete after the reminder fires.
_DEPOSIT_INFO_MESSAGE_IDS: dict[int, list[int]] = {}
_deposit_reminder_app: Any | None = None

_PAYMENT_BOUND_MODELS = (
    VenmoPayment,
    CashAppPayment,
    PayPalPayment,
    ZellePayment,
    CryptoPayment,
)


def _reset_deposit_info_messages(chat_id: int) -> None:
    _DEPOSIT_INFO_MESSAGE_IDS[int(chat_id)] = []


def _track_deposit_info_message(chat_id: int, message_id: int | None) -> None:
    if message_id is None:
        return
    tracked = _DEPOSIT_INFO_MESSAGE_IDS.setdefault(int(chat_id), [])
    tracked.append(int(message_id))


def _track_deposit_info_messages(chat_id: int, message_ids: list[int]) -> None:
    for message_id in message_ids:
        _track_deposit_info_message(chat_id, message_id)


async def _delete_deposit_info_messages(bot, chat_id: int) -> None:
    message_ids = _DEPOSIT_INFO_MESSAGE_IDS.pop(int(chat_id), [])
    for message_id in message_ids:
        try:
            await bot.delete_message(chat_id=int(chat_id), message_id=int(message_id))
        except Exception:
            logger.debug(
                "Could not delete deposit info message chat_id=%s message_id=%s",
                chat_id,
                message_id,
                exc_info=True,
            )


async def _deposit_send_message(chat, chat_id: int, **kwargs):
    sent = await chat.send_message(**kwargs)
    _track_deposit_info_message(chat_id, sent.message_id)
    return sent


def _bind_attempt_job_name(attempt_id: int) -> str:
    return f"bind_attempt_expire_{attempt_id}"


async def _bind_attempt_expire_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    attempt_id = context.job.data.get("attempt_id") if context.job.data else None
    if attempt_id is not None:
        expire_bind_attempt(int(attempt_id))


def _schedule_bind_attempt_expiry(
    context: ContextTypes.DEFAULT_TYPE,
    attempt_id: int,
) -> None:
    try:
        name = _bind_attempt_job_name(attempt_id)
        for job in context.job_queue.get_jobs_by_name(name):
            job.schedule_removal()
        context.job_queue.run_once(
            _bind_attempt_expire_callback,
            when=BIND_ATTEMPT_TTL_SECONDS,
            data={"attempt_id": int(attempt_id)},
            name=name,
        )
    except Exception:
        logger.warning(
            "Failed to schedule bind attempt expiry attempt_id=%s",
            attempt_id,
            exc_info=True,
        )


def _merged_deposit_variant_response(
    variant_data: dict,
    method: dict,
    *,
    tier: dict | None = None,
) -> dict:
    merged = _merge_response_layers(variant_data, tier, method) if tier else _merge_response_layers(variant_data, method)
    return _with_method_checkout_settings(merged, method, tier=tier)


def _pick_deposit_variant_response(
    method_id: int,
    method: dict,
    amount,
    *,
    chat_id: int | None = None,
    method_slug: str = "",
) -> tuple[dict | None, dict | None]:
    """Return (response_data, tier) for a deposit method selection."""
    tier = get_tier_for_amount(method_id, amount) if isinstance(amount, Decimal) else None
    sticky_variant_id: int | None = None
    slug_norm = (method_slug or "").strip().lower()
    if slug_norm in ("venmo", "zelle", "cashapp", "paypal") and chat_id is not None:
        binding = get_chat_binding(int(chat_id), slug_norm)
        if binding and binding.variant_id:
            sticky_variant_id = binding.variant_id
    if slug_norm == "cashapp":
        sticky_variant_id = None

    if tier:
        response_data = pick_variant(
            method_id,
            tier_id=tier["id"],
            variant_id=sticky_variant_id,
        )
        if not response_data:
            return None, tier
        return _merged_deposit_variant_response(response_data, method, tier=tier), tier

    response_data = pick_variant(method_id, variant_id=sticky_variant_id)
    if response_data:
        return _merged_deposit_variant_response(response_data, method), None
    merged = _merge_response_layers(method, method)
    return _with_method_checkout_settings(merged, method), None


async def _deposit_send_html_or_plain(
    chat,
    chat_id: int,
    *,
    html_text: str,
    plain_text: str,
    log_label: str,
    disable_web_page_preview: bool | None = None,
    reply_markup=None,
):
    kwargs: dict = {"text": html_text, "parse_mode": "HTML"}
    if disable_web_page_preview is not None:
        kwargs["disable_web_page_preview"] = disable_web_page_preview
    if reply_markup is not None:
        kwargs["reply_markup"] = reply_markup
    try:
        return await _deposit_send_message(chat, int(chat_id), **kwargs)
    except Exception:
        logger.warning(
            "%s: HTML message failed, retrying plain chat_id=%s",
            log_label,
            chat_id,
            exc_info=True,
        )
        plain_kwargs: dict = {"text": plain_text}
        if reply_markup is not None:
            plain_kwargs["reply_markup"] = reply_markup
        return await _deposit_send_message(chat, int(chat_id), **plain_kwargs)


def _first_time_setup_ack_markup(*, attempt_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "I HAVE READ THE INSTRUCTIONS ABOVE",
                    callback_data=f"depft:{attempt_id}",
                )
            ]
        ]
    )


async def _send_first_time_payment_destination(
    chat,
    chat_id: int,
    *,
    response_data: dict,
) -> bool:
    if not _response_data_has_content(response_data):
        logger.warning(
            "first_time_destination: empty dashboard response chat_id=%s",
            chat_id,
        )
        return False
    _track_deposit_info_messages(
        int(chat_id),
        await send_response_messages(chat, response_data),
    )
    return True


async def _send_first_time_method_setup(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    method_id: int,
    method: dict,
    method_slug: str,
    amount: Decimal,
    tier: dict | None,
    response_data: dict,
    bind_kind: str,
) -> bool | str:
    chat_id = context.chat_data.get("deposit_chat_id") or (
        query.message.chat.id if query.message else None
    )
    club_id = context.chat_data.get("deposit_club_id")
    user_id = context.chat_data.get("deposit_user_id")
    slug = (method_slug or "").strip().lower()
    method_name = method.get("name") or slug

    if chat_id is None or club_id is None:
        await query.edit_message_text(
            f"This group is not linked to a club. {method_name} setup cannot be started."
        )
        return False

    variant_id = response_data.get("variant_id")
    if not variant_id:
        await query.edit_message_text(
            f"{method_name} is not configured for this club. Please contact support."
        )
        return False

    tier_id = int(tier["id"]) if tier else None
    deposit_amount_cents: int | None = None
    if bind_kind == BIND_KIND_SPECIAL_AMOUNT:
        deposit_amount_cents = deposit_amount_to_cents(amount)

    try:
        attempt = start_bind_attempt(
            telegram_chat_id=int(chat_id),
            club_id=int(club_id),
            payment_method_slug=slug,
            method_id=int(method_id),
            tier_id=tier_id,
            variant_id=int(variant_id),
            bind_kind=bind_kind,
            deposit_amount_cents=deposit_amount_cents,
            initiated_by_telegram_user_id=int(user_id) if user_id else None,
        )
    except ValueError as e:
        await query.edit_message_text(str(e))
        return False

    _schedule_bind_attempt_expiry(context, attempt.id)

    _reset_deposit_info_messages(int(chat_id))
    await query.edit_message_text(
        f"Deposit via {method_name} — one-time setup"
    )
    if query.message:
        _track_deposit_info_message(int(chat_id), query.message.message_id)
        register_flow_callback_message(context, query.message.message_id, flow="deposit")

    context.chat_data["deposit_setup_attempt_id"] = attempt.id
    context.chat_data["deposit_setup_response_data"] = _prepare_deposit_response_data(
        response_data,
        method_slug=slug,
        method=method,
        tier=tier,
        allow_destination_fallback=False,
    )

    chat = query.message.chat
    if bind_kind == BIND_KIND_MEMO_EMOJI and attempt.setup_emoji:
        setup_msg = await _deposit_send_html_or_plain(
            chat,
            int(chat_id),
            html_text=format_first_time_memo_instructions_message(
                payment_method_slug=slug,
                setup_code=attempt.setup_emoji,
                use_html=True,
            ),
            plain_text=format_first_time_memo_instructions_message(
                payment_method_slug=slug,
                setup_code=attempt.setup_emoji,
                use_html=False,
            ),
            log_label="memo_setup_instructions",
            reply_markup=_first_time_setup_ack_markup(attempt_id=attempt.id),
        )
    else:
        assert deposit_amount_cents is not None and attempt.amount_cents is not None
        setup_msg = await _deposit_send_html_or_plain(
            chat,
            int(chat_id),
            html_text=format_first_time_special_amount_setup_message(
                payment_method_slug=slug,
                setup_amount_cents=attempt.amount_cents,
                chosen_amount_cents=deposit_amount_cents,
                use_html=True,
            ),
            plain_text=format_first_time_special_amount_setup_message(
                payment_method_slug=slug,
                setup_amount_cents=attempt.amount_cents,
                chosen_amount_cents=deposit_amount_cents,
                use_html=False,
            ),
            log_label="amount_setup",
            reply_markup=_first_time_setup_ack_markup(attempt_id=attempt.id),
        )
    if setup_msg is not None:
        register_flow_callback_message(
            context, setup_msg.message_id, flow="deposit"
        )

    _schedule_deposit_reminder(context, club_id, int(chat_id), user_id)
    return "await_ack"


def _reminder_job_name(chat_id: int | str) -> str:
    return f"deposit_reminder_{chat_id}"


def register_deposit_reminder_runtime(app: Any) -> None:
    """Store Application for cancel_deposit_reminder_for_chat outside handlers."""
    global _deposit_reminder_app
    _deposit_reminder_app = app


def _chat_has_payment_bound_since(chat_id: int, since: datetime) -> bool:
    """True if any payment was bound to this GC at or after since (UTC)."""
    since_utc = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
    with get_db() as session:
        for model in _PAYMENT_BOUND_MODELS:
            row = (
                session.query(model)
                .filter(
                    model.telegram_chat_id == int(chat_id),
                    model.bound_at.isnot(None),
                    model.bound_at >= since_utc,
                )
                .first()
            )
            if row is not None:
                return True
    return False


def _chat_has_stripe_checkout_completed_since(chat_id: int, since: datetime) -> bool:
    """True if a Stripe checkout completed for this GC at or after since (UTC)."""
    since_utc = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
    with get_db() as session:
        row = (
            session.query(StripeCheckoutSession)
            .filter(
                StripeCheckoutSession.telegram_chat_id == int(chat_id),
                StripeCheckoutSession.status == "complete",
                StripeCheckoutSession.completed_at.isnot(None),
                StripeCheckoutSession.completed_at >= since_utc,
            )
            .first()
        )
        return row is not None


def _chat_has_deposit_activity_since(chat_id: int, since: datetime) -> bool:
    """True if chips were added (/add) in this GC at or after since (UTC)."""
    since_utc = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
    with get_db() as session:
        row = (
            session.query(PlayerActivity)
            .filter(
                PlayerActivity.chat_id == int(chat_id),
                PlayerActivity.activity_type == "deposit",
                PlayerActivity.cancelled.is_(False),
                PlayerActivity.created_at >= since_utc,
            )
            .first()
        )
        return row is not None


def _should_skip_deposit_reminder(chat_id: int, since: datetime) -> bool:
    """True when payment received, Stripe checkout, or /add completed since schedule."""
    return (
        _chat_has_payment_bound_since(chat_id, since)
        or _chat_has_stripe_checkout_completed_since(chat_id, since)
        or _chat_has_deposit_activity_since(chat_id, since)
    )


def _parse_scheduled_at(job_data: dict | None) -> datetime | None:
    raw = (job_data or {}).get("scheduled_at")
    if not raw:
        return None
    try:
        scheduled_at = datetime.fromisoformat(str(raw))
    except (ValueError, TypeError):
        return None
    if scheduled_at.tzinfo is None:
        return scheduled_at.replace(tzinfo=timezone.utc)
    return scheduled_at


async def _deposit_reminder_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job queue callback: delete deposit instructions and nudge the customer."""
    chat_id = int(context.job.chat_id)
    job_data = context.job.data or {}
    club_id = job_data.get("club_id")
    _PENDING_DEPOSIT_REMINDERS.pop(chat_id, None)

    scheduled_at = _parse_scheduled_at(job_data)
    if scheduled_at and _should_skip_deposit_reminder(chat_id, scheduled_at):
        logger.info(
            "deposit_reminder skipped chat_id=%s deposit completed since schedule",
            chat_id,
        )
        await _delete_deposit_info_messages(context.bot, chat_id)
        return

    tracked_count = len(_DEPOSIT_INFO_MESSAGE_IDS.get(chat_id, []))
    logger.info(
        "deposit_reminder firing chat_id=%s tracked_messages=%s",
        chat_id,
        tracked_count,
    )
    await _delete_deposit_info_messages(context.bot, chat_id)

    methods = get_deposit_method_names(club_id) if club_id else []
    method_list = ", ".join(methods) if methods else ""

    text = (
        "Hey! Just checking in \u2014 if you haven\u2019t completed your deposit yet, "
        "feel free to reach out and we\u2019ll help you get it done!\n\n"
        "You can also start a new deposit anytime with /deposit."
    )
    if method_list:
        text += f"\n\nWe offer: {method_list}."
    text += "\n\nDeposits are available 24/7!"

    try:
        await context.bot.send_message(chat_id=chat_id, text=text)
    except Exception:
        logger.warning("Failed to send deposit reminder to chat_id=%s", chat_id, exc_info=True)


def _schedule_deposit_reminder(
    context: ContextTypes.DEFAULT_TYPE,
    club_id: int | None,
    chat_id: int | None,
    user_id: int | None,
) -> None:
    """Schedule a follow-up reminder 10 minutes after a deposit completes."""
    if not club_id or not chat_id:
        return
    try:
        name = _reminder_job_name(chat_id)
        for job in context.job_queue.get_jobs_by_name(name):
            job.schedule_removal()
        context.job_queue.run_once(
            _deposit_reminder_callback,
            when=DEPOSIT_REMINDER_SECONDS,
            chat_id=int(chat_id),
            data={
                "club_id": club_id,
                "scheduled_at": datetime.now(timezone.utc).isoformat(),
            },
            name=name,
        )
        if user_id:
            _PENDING_DEPOSIT_REMINDERS[int(chat_id)] = int(user_id)
        logger.info(
            "deposit_reminder scheduled chat_id=%s in %ss tracked_messages=%s",
            chat_id,
            DEPOSIT_REMINDER_SECONDS,
            len(_DEPOSIT_INFO_MESSAGE_IDS.get(int(chat_id), [])),
        )
    except Exception:
        logger.warning(
            "Failed to schedule deposit reminder chat_id=%s", chat_id, exc_info=True
        )


def cancel_deposit_reminder_for_chat(
    chat_id: int | str,
    *,
    job_queue: Any | None = None,
) -> None:
    """Cancel pending deposit follow-up for a chat (handlers, payment notify, etc.)."""
    _PENDING_DEPOSIT_REMINDERS.pop(int(chat_id), None)
    _DEPOSIT_INFO_MESSAGE_IDS.pop(int(chat_id), None)
    queue = job_queue
    if queue is None and _deposit_reminder_app is not None:
        queue = getattr(_deposit_reminder_app, "job_queue", None)
    if queue is None:
        return
    try:
        for job in queue.get_jobs_by_name(_reminder_job_name(chat_id)):
            job.schedule_removal()
    except Exception:
        pass


def _cancel_deposit_reminder(context: ContextTypes.DEFAULT_TYPE, chat_id: int | str) -> None:
    """Cancel any pending deposit follow-up reminder for a chat."""
    cancel_deposit_reminder_for_chat(chat_id, job_queue=context.job_queue)


def cancel_deposit_reminder(context: ContextTypes.DEFAULT_TYPE, chat_id: int | str) -> None:
    """Public entry: cancel pending deposit follow-up reminder for a chat."""
    _cancel_deposit_reminder(context, chat_id)


def _can_cancel_reminder_as_staff(user_id: int, club_id: int) -> bool:
    if is_club_staff(user_id, club_id):
        return True
    if user_id in ADMIN_USER_IDS:
        return get_club_allows_admin_commands(club_id)
    return False


async def cancel_deposit_reminder_on_customer_msg(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Group handler (group=2): cancel the pending reminder when the depositing customer responds."""
    if not update.message or not update.effective_chat or not update.effective_user:
        return
    chat_id = update.effective_chat.id
    expected_user = _PENDING_DEPOSIT_REMINDERS.get(chat_id)
    if expected_user is None:
        return
    if update.effective_user.id != expected_user:
        return
    _cancel_deposit_reminder(context, chat_id)


async def cancel_deposit_reminder_on_group_activity(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Group handler (group=2): cancel on payment-received bot post or staff 'added' message."""
    if not update.message or not update.effective_chat or not update.effective_user:
        return
    text = (update.message.text or update.message.caption or "").strip()
    if not text:
        return

    chat_id = update.effective_chat.id
    lowered = text.lower()
    user = update.effective_user

    if user.is_bot:
        if _PAYMENT_RECEIVED_SNIPPET in lowered:
            _cancel_deposit_reminder(context, chat_id)
        return

    if "added" not in lowered:
        return

    club_id = get_club_for_chat(chat_id)
    if club_id is None:
        return
    if not _can_cancel_reminder_as_staff(user.id, club_id):
        return
    _cancel_deposit_reminder(context, chat_id)


def _is_usable_stripe_response_text(text: str | None) -> bool:
    cleaned = (text or "").strip()
    if len(cleaned) < 24:
        return False
    if cleaned.lower() in _PLACEHOLDER_RESPONSE_TEXT:
        return False
    return True


def _stripe_response_text_from_layers(*layers: dict | None) -> str | None:
    for layer in layers:
        if not layer:
            continue
        candidate = (layer.get("response_text") or "").strip()
        if _is_usable_stripe_response_text(candidate):
            return candidate
    return None


def _ensure_hyperlink_placeholder(text: str) -> str:
    if "{{hyperlink}}" in text:
        return text
    return f"{text.rstrip()}\n\n{{{{hyperlink}}}}"


def _apply_hardcoded_stripe_below_100(
    response_data: dict,
    *,
    method_slug: str,
    amount,
    method: dict | None = None,
    tier: dict | None = None,
) -> dict:
    """Force Stripe checkout for Apple Pay deposits up to $100."""
    slug = (method_slug or "").strip().lower()
    if slug not in _STRIPE_HARDCODE_SLUGS:
        return response_data
    if not isinstance(amount, Decimal) or amount > _STRIPE_HARDCODE_MAX:
        return response_data
    merged = dict(response_data)
    merged["use_group_checkout_link"] = True
    merged["group_checkout_provider"] = "stripe"
    merged.setdefault("hyperlink_text", "PAY HERE")

    text = _stripe_response_text_from_layers(merged, tier, method)
    if not text:
        text = _STRIPE_HARDCODE_DEFAULT_TEXT
    merged["response_text"] = _ensure_hyperlink_placeholder(text)
    merged["response_type"] = "text"
    return merged


def _stripe_checkout_enabled(response_data: dict) -> bool:
    if not response_data.get("use_group_checkout_link"):
        return False
    provider = (response_data.get("group_checkout_provider") or "stripe").strip().lower()
    return provider == "stripe"


def _response_has_hyperlink_placeholder(response_data: dict) -> bool:
    for field in ("response_text", "response_caption"):
        if "{{hyperlink}}" in (response_data.get(field) or ""):
            return True
    return False


def _build_stripe_response_payload(response_data: dict, checkout_url: str) -> dict:
    hyperlink_text = (response_data.get("hyperlink_text") or "PAY HERE").strip() or "PAY HERE"
    safe_url = html.escape(checkout_url, quote=True)
    safe_label = html.escape(hyperlink_text, quote=False)
    html_link = f'<a href="{safe_url}">{safe_label}</a>'

    payload = dict(response_data)
    rtype = payload.get("response_type") or "text"
    if rtype == "text":
        text_field = "response_text"
        template = (payload.get("response_text") or "").strip()
    else:
        text_field = "response_caption"
        template = (payload.get("response_caption") or "").strip()

    if "{{hyperlink}}" in template:
        html_text = template.replace("{{hyperlink}}", html_link)
        plain_text = template.replace("{{hyperlink}}", checkout_url)
        payload[text_field] = html_text
        payload["parse_mode"] = "HTML"
        payload["disable_web_page_preview"] = True
        payload["_stripe_plain_fallback"] = plain_text
        payload["_stripe_plain_field"] = text_field
    elif template:
        payload[text_field] = template
        payload["_stripe_link_only_html"] = html_link
        payload["_stripe_link_only_plain"] = checkout_url
    else:
        payload[text_field] = html_link
        payload["parse_mode"] = "HTML"
        payload["disable_web_page_preview"] = True
        payload["_stripe_plain_fallback"] = checkout_url
        payload["_stripe_plain_field"] = text_field
    return payload


def _mark_awaiting_amount(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.chat_data["deposit_awaiting_amount"] = True
    chat_id = context.chat_data.get("deposit_chat_id")
    if chat_id is not None:
        _DEPOSIT_AWAITING_CHATS.add(int(chat_id))


def _clear_awaiting_amount(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.chat_data.pop("deposit_awaiting_amount", None)
    chat_id = context.chat_data.get("deposit_chat_id")
    if chat_id is not None:
        _DEPOSIT_AWAITING_CHATS.discard(int(chat_id))


def _is_awaiting_amount(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> bool:
    return bool(context.chat_data.get("deposit_awaiting_amount")) or chat_id in _DEPOSIT_AWAITING_CHATS


async def _ask_deposit_amount(message, context: ContextTypes.DEFAULT_TYPE) -> int:
    _mark_awaiting_amount(context)
    await message.reply_text("How much would you like to deposit?")
    return DEPOSIT_AMOUNT


def _sync_deposit_conv_state(
    update: Update, context: ContextTypes.DEFAULT_TYPE, new_state: object
) -> None:
    """Keep ConversationHandler state aligned when amount is handled via the test-bot safety net."""
    if new_state is None:
        return
    app = context.application
    for handlers in app.handlers.values():
        for handler in handlers:
            if getattr(handler, "name", None) != "deposit_conv":
                continue
            try:
                key = handler._get_key(update)
            except RuntimeError:
                return
            if new_state == ConversationHandler.END:
                handler._conversations.pop(key, None)
            else:
                handler._update_state(new_state, key)
            return


async def deposit_amount_priority_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Test-bot: intercept amount replies before other ConversationHandlers can swallow them."""
    if not is_test_bot_worker():
        return
    if not update.message or not update.effective_chat:
        return
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        return
    text = (update.message.text or "").strip()
    if not text or text.startswith("/"):
        return

    chat_id = chat.id
    if is_update_too_old(update):
        log_stale_update(update, handler="deposit_amount_priority")
        return
    if not _is_awaiting_amount(context, chat_id):
        return
    if context.chat_data.get("deposit_amount"):
        return
    sender_id = update.effective_user.id if update.effective_user else None
    if not deposit_amount_actor_allowed(
        context, sender_id=sender_id, text=update.message.text
    ):
        return

    logger.info(
        "deposit_amount_priority chat_id=%s user_id=%s text=%r chat_data_keys=%s",
        chat_id,
        update.effective_user.id if update.effective_user else None,
        text[:50],
        sorted(context.chat_data.keys()),
    )
    new_state = await deposit_amount_received(update, context)
    _sync_deposit_conv_state(update, context, new_state)
    _clear_awaiting_amount(context)
    raise ApplicationHandlerStop


def _no_deposit_methods_message(club_id: int | None, amount: Decimal) -> str:
    if club_id is None:
        return f"No deposit methods available for ${amount}."
    backend = "v2 club_payment_*" if use_payment_v2() else "legacy payment_methods"
    lowest = get_lowest_minimum(club_id, "deposit")
    if lowest is not None and amount < lowest:
        return f"Sorry! The minimum deposit amount is ${lowest:,.2f}."
    return (
        f"No deposit methods available for ${amount}.\n"
        f"(club_id={club_id}, backend={backend})\n"
        "Add methods in the dashboard or run a v2 seed script."
    )


async def deposit_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat or not update.effective_user:
        return ConversationHandler.END
    if is_update_too_old(update):
        log_stale_update(update, handler="deposit_entry")
        return ConversationHandler.END
    _cleanup(context)
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Use /deposit in a club group.")
        return ConversationHandler.END
    club_id = get_club_for_chat(chat.id)
    if club_id is None:
        await update.message.reply_text(
            "This group isn't linked to a club yet. The club owner must add the bot."
        )
        return ConversationHandler.END

    update_group_name(chat.id, chat.title)
    _cancel_deposit_reminder(context, chat.id)

    user_id = update.effective_user.id
    try:
        record_activity(club_id, user_id, chat.id, "dep_cmd")
    except Exception:
        logger.warning(
            "deposit_entry: record dep_cmd failed chat_id=%s club_id=%s",
            chat.id,
            club_id,
            exc_info=True,
        )
    is_bot_admin = user_id in ADMIN_USER_IDS

    if is_test_bot_worker():
        context.chat_data.pop("deposit_admin_initiated", None)
        context.chat_data.pop("deposit_admin_user_id", None)

    context.chat_data["deposit_club_id"] = club_id
    context.chat_data["deposit_chat_id"] = chat.id

    if is_bot_admin and not is_test_bot_worker():
        if not get_club_allows_admin_commands(club_id):
            return ConversationHandler.END
        context.chat_data["deposit_admin_initiated"] = True
        context.chat_data["deposit_admin_user_id"] = user_id
        _ensure_deposit_session(context)
        _record_funnel_from_context(context, STEP_DEPOSIT_STARTED)

        simple = get_club_simple_mode(club_id, "deposit")
        if simple:
            await _send_simple_response(update.message, simple)
            _record_funnel_from_context(context, STEP_INSTRUCTIONS_SENT)
            _schedule_deposit_reminder(context, club_id, chat.id, user_id=None)
            _cleanup(context)
            return ConversationHandler.END

        mark_active_flow(context, "deposit")
        logger.info(
            "deposit_entry admin-initiated chat_id=%s admin_id=%s -> DEPOSIT_AMOUNT",
            chat.id,
            user_id,
        )
        return await _ask_deposit_amount(update.message, context)

    claimed = is_first_deposit_claimed(chat.id)
    first = False if claimed else is_first_deposit(club_id, user_id)
    settings = get_first_deposit_settings(club_id) if first else None

    context.chat_data["deposit_club_id"] = club_id
    context.chat_data["deposit_chat_id"] = chat.id
    context.chat_data["deposit_user_id"] = user_id
    context.chat_data["deposit_is_first"] = first
    context.chat_data["deposit_fd_settings"] = settings
    _ensure_deposit_session(context)
    _record_funnel_from_context(context, STEP_DEPOSIT_STARTED)

    simple = get_club_simple_mode(club_id, "deposit")
    if simple:
        context.chat_data["deposit_simple_data"] = simple

    if first and settings and settings["referral_enabled"]:
        mark_active_flow(context, "deposit")
        await update.message.reply_text(
            "Welcome to the club! How did you hear about us? "
            "If it was a player, please type their GG username."
        )
        return DEPOSIT_REFERRAL

    if simple:
        return await _finish_simple_deposit(update.message, context)

    mark_active_flow(context, "deposit")
    logger.info(
        "deposit_entry chat_id=%s user_id=%s test=%s -> DEPOSIT_AMOUNT",
        chat.id,
        user_id,
        is_test_bot_worker(),
    )
    return await _ask_deposit_amount(update.message, context)


async def deposit_referral_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return ConversationHandler.END
    if is_update_too_old(update):
        log_stale_update(update, handler="deposit_referral_received")
        _cleanup(context)
        return ConversationHandler.END

    simple = context.chat_data.get("deposit_simple_data")
    if simple:
        return await _finish_simple_deposit(update.message, context)

    _record_funnel_from_context(context, STEP_REFERRAL_COMPLETED)
    return await _ask_deposit_amount(update.message, context)


async def deposit_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat:
        return ConversationHandler.END
    if is_update_too_old(update):
        log_stale_update(update, handler="deposit_amount_received")
        _cleanup(context)
        return ConversationHandler.END

    admin_uid = context.chat_data.get("deposit_admin_user_id")
    sender_id = update.effective_user.id if update.effective_user else None
    message_text = update.message.text or ""

    if not deposit_amount_actor_allowed(
        context, sender_id=sender_id, text=message_text
    ):
        return DEPOSIT_AMOUNT

    logger.info(
        "deposit_amount_received text=%r chat_id=%s user_id=%s awaiting=%s admin_uid=%s test=%s",
        message_text[:30],
        update.effective_chat.id,
        sender_id,
        context.chat_data.get("deposit_awaiting_amount"),
        admin_uid,
        is_test_bot_worker(),
    )

    _clear_awaiting_amount(context)

    # In admin-initiated flows, only record a non-admin as the depositor.
    if update.effective_user:
        uid = update.effective_user.id
        if context.chat_data.get("deposit_admin_initiated"):
            if uid not in ADMIN_USER_IDS:
                context.chat_data["deposit_user_id"] = uid
        elif not context.chat_data.get("deposit_user_id"):
            context.chat_data["deposit_user_id"] = uid

    club_id = context.chat_data.get("deposit_club_id")
    if not club_id:
        await update.message.reply_text(
            "Deposit session expired or was not started. Use /deposit again."
        )
        return ConversationHandler.END

    raw = message_text.strip().replace("$", "").replace(",", "")
    try:
        amount = Decimal(raw)
        if amount <= 0:
            raise InvalidOperation()
    except (InvalidOperation, Exception):
        if looks_like_amount(message_text) and deposit_amount_show_validation_error(
            context, sender_id=sender_id
        ):
            await update.message.reply_text(
                "Please enter a valid dollar amount (Example: 50 or 100.00)."
            )
        return DEPOSIT_AMOUNT

    context.chat_data["deposit_amount"] = amount
    _record_funnel_from_context(context, STEP_AMOUNT_ENTERED)
    try:
        methods = get_methods_for_amount(club_id, "deposit", amount)
    except Exception:
        logger.exception(
            "deposit_amount_received: failed loading methods club_id=%s amount=%s v2=%s",
            club_id,
            amount,
            use_payment_v2(),
        )
        await update.message.reply_text(
            "Could not load deposit methods. Check bot logs and DATABASE_URL."
        )
        return ConversationHandler.END

    logger.info(
        "deposit_amount_received chat_id=%s club_id=%s amount=%s methods=%s v2=%s test=%s",
        update.effective_chat.id,
        club_id,
        amount,
        [m.get("slug") for m in methods],
        use_payment_v2(),
        is_test_bot_worker(),
    )

    if not methods:
        await update.message.reply_text(_no_deposit_methods_message(club_id, amount))
        return ConversationHandler.END

    try:
        if is_round_table_club(int(club_id)):
            await _prompt_deposit_union(update.message, context)
            return DEPOSIT_UNION

        await _prompt_deposit_methods(update.message, context, amount=amount)
        return DEPOSIT_CHOOSE
    except Exception:
        logger.exception(
            "deposit_amount_received: failed prompting methods club_id=%s amount=%s",
            club_id,
            amount,
        )
        await update.message.reply_text(
            "Something went wrong showing deposit methods. Try /deposit again."
        )
        return ConversationHandler.END


async def deposit_union_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        return ConversationHandler.END
    query = update.callback_query
    if await handle_stale_flow_callback(
        update,
        context,
        flow="deposit",
        handler="deposit_union_chosen",
        cleanup=_cleanup,
    ):
        return ConversationHandler.END
    await query.answer()

    data = query.data or ""
    if not data.startswith("depunion:"):
        return DEPOSIT_UNION

    shorthand = data.split(":", 1)[1].strip().upper()
    label = union_label_for_shorthand(shorthand)
    if not label:
        await query.edit_message_text("That option is no longer available.")
        return ConversationHandler.END

    context.chat_data["deposit_union_shorthand"] = shorthand
    context.chat_data["deposit_union_label"] = label
    _record_funnel_from_context(
        context,
        STEP_UNION_CHOSEN,
        metadata={"union_shorthand": shorthand, "union_label": label},
    )

    amount = context.chat_data.get("deposit_amount")
    if not isinstance(amount, Decimal):
        await query.edit_message_text("Deposit session expired. Use /deposit again.")
        _cleanup(context)
        return ConversationHandler.END

    await _prompt_deposit_methods(
        query.message,
        context,
        amount=amount,
        edit_message=query,
    )
    return DEPOSIT_CHOOSE


def _deposit_method_buttons(methods) -> list[list[InlineKeyboardButton]]:
    buttons = []
    row = []
    for m in methods:
        row.append(InlineKeyboardButton(m["name"], callback_data=f"dep:{m['id']}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return buttons


async def _prompt_deposit_union(message, context) -> None:
    buttons = [
        [
            InlineKeyboardButton(
                union["label"], callback_data=f"depunion:{union['shorthand']}"
            )
        ]
        for union in ROUND_TABLE_DEPOSIT_UNIONS
    ]
    sent = await message.reply_text(
        "Which club would you like your deposit to be added to?",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    register_flow_callback_message(context, sent.message_id, flow="deposit")


async def _prompt_deposit_methods(
    message,
    context,
    *,
    amount: Decimal,
    edit_message=None,
) -> None:
    club_id = context.chat_data.get("deposit_club_id")
    methods = get_methods_for_amount(club_id, "deposit", amount)
    if not methods:
        text = _no_deposit_methods_message(club_id, amount)
        if edit_message is not None:
            await edit_message.edit_message_text(text)
        else:
            await message.reply_text(text)
        return
    text = f"Deposit amount: ${amount}\nSelect your deposit method:"
    markup = InlineKeyboardMarkup(_deposit_method_buttons(methods))
    if edit_message is not None:
        await edit_message.edit_message_text(text, reply_markup=markup)
        if edit_message.message is not None:
            register_flow_callback_message(
                context, edit_message.message.message_id, flow="deposit"
            )
    else:
        sent = await message.reply_text(text, reply_markup=markup)
        register_flow_callback_message(context, sent.message_id, flow="deposit")


async def _run_first_time_method_setup_from_choice(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    method_id: int,
    method: dict,
    method_slug: str,
    bind_kind: str,
) -> int | str:
    amount = context.chat_data.get("deposit_amount", "?")
    chat_id = query.message.chat.id if query.message else None
    if not isinstance(amount, Decimal):
        await query.edit_message_text("Deposit session expired. Use /deposit again.")
        return ConversationHandler.END
    response_data, tier = _pick_deposit_variant_response(
        method_id,
        method,
        amount,
        chat_id=int(chat_id) if chat_id is not None else None,
        method_slug=method_slug,
    )
    if not response_data and tier:
        logger.error(
            "deposit_method_no_variants chat_id=%s method_id=%s slug=%r setup",
            chat_id,
            method_id,
            method_slug,
        )
        await query.edit_message_text(
            "This payment method is not configured yet. Please contact support."
        )
        return ConversationHandler.END
    if _stripe_checkout_enabled(response_data or {}):
        return await _run_normal_deposit_from_choice(
            query,
            context,
            method_id=method_id,
            method=method,
            method_slug=method_slug,
            picked=(response_data, tier),
        )
    result = await _send_first_time_method_setup(
        query,
        context,
        method_id=method_id,
        method=method,
        method_slug=method_slug,
        amount=amount,
        tier=tier,
        response_data=response_data,
        bind_kind=bind_kind,
    )
    if result == "await_ack":
        return DEPOSIT_SETUP_ACK
    if not result:
        return ConversationHandler.END
    return await _complete_deposit_flow(query.message.chat, context)


async def _run_normal_deposit_from_choice(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    method_id: int,
    method: dict,
    method_slug: str,
    picked: tuple[dict | None, dict | None] | None = None,
) -> int | str:
    amount = context.chat_data.get("deposit_amount", "?")
    chat_id = query.message.chat.id if query.message else None
    if picked is not None:
        response_data, tier = picked
    else:
        response_data, tier = _pick_deposit_variant_response(
            method_id,
            method,
            amount,
            chat_id=chat_id,
            method_slug=method_slug,
        )
    if not response_data and tier:
        logger.error(
            "deposit_method_no_variants chat_id=%s method_id=%s tier_id=%s slug=%r",
            chat_id,
            method_id,
            tier.get("id"),
            method_slug,
        )
        await query.edit_message_text(
            "This payment method is not configured yet. Please contact support."
        )
        return ConversationHandler.END
    ok = await _send_deposit_method_response(
        query,
        context,
        amount=amount,
        display_name=method["name"],
        method_id=method_id,
        method_slug=method_slug,
        response_data=response_data,
        method=method,
        tier=tier,
    )
    if not ok:
        return ConversationHandler.END
    if isinstance(amount, Decimal):
        try:
            record_method_deposit(method_id, amount)
        except Exception:
            pass
    return await _complete_deposit_flow(query.message.chat, context)


async def deposit_method_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        return ConversationHandler.END
    query = update.callback_query
    if await handle_stale_flow_callback(
        update,
        context,
        flow="deposit",
        handler="deposit_method_chosen",
        cleanup=_cleanup,
    ):
        return ConversationHandler.END
    await query.answer()

    data = query.data or ""
    if not data.startswith("dep:"):
        return ConversationHandler.END

    method_id = int(data.split(":")[1])
    method = get_method_by_id(method_id)
    if not method:
        await query.edit_message_text("That method is no longer available.")
        return ConversationHandler.END

    method_slug = (method.get("slug") or "").strip().lower()
    logger.info(
        "deposit_method_chosen chat_id=%s method_id=%s slug=%r name=%r has_sub_options=%s stripe_configured=%s",
        query.message.chat.id if query.message else None,
        method_id,
        method_slug,
        method.get("name"),
        method.get("has_sub_options"),
        stripe_configured(),
    )

    context.chat_data["deposit_method_name"] = method["name"]
    context.chat_data["deposit_method_id"] = method_id

    if method["has_sub_options"]:
        subs = get_sub_options(method_id)
        if subs:
            buttons = []
            row = []
            for s in subs:
                row.append(
                    InlineKeyboardButton(s["name"], callback_data=f"depsub:{s['id']}")
                )
                if len(row) == 2:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)
            await query.edit_message_text(
                f"You selected {method['name']}. Which option?",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return DEPOSIT_SUB

    _record_funnel_from_context(context, STEP_METHOD_CHOSEN, method_slug=method_slug)

    amount = context.chat_data.get("deposit_amount", "?")
    chat_id = query.message.chat.id if query.message else None

    club_id = context.chat_data.get("deposit_club_id")
    bind_kind = (
        bind_mode_for_method(method_slug, club_id=club_id)
        if chat_id is not None
        else None
    )
    chat_bound = (
        is_chat_method_bound(int(chat_id), method_slug)
        if chat_id is not None
        else False
    )

    if bind_kind and chat_id is not None and not chat_bound:
        context.chat_data["deposit_requires_method_setup"] = True
        return await _run_first_time_method_setup_from_choice(
            query,
            context,
            method_id=method_id,
            method=method,
            method_slug=method_slug,
            bind_kind=bind_kind,
        )

    return await _run_normal_deposit_from_choice(
        query,
        context,
        method_id=method_id,
        method=method,
        method_slug=method_slug,
    )


async def deposit_setup_ack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        return ConversationHandler.END
    query = update.callback_query
    if await handle_stale_flow_callback(
        update,
        context,
        flow="deposit",
        handler="deposit_setup_ack",
        cleanup=_cleanup,
    ):
        return ConversationHandler.END
    data = query.data or ""
    if not data.startswith("depft:"):
        return ConversationHandler.END

    expected_user = context.chat_data.get("deposit_user_id")
    if expected_user is not None and query.from_user.id != int(expected_user):
        await query.answer(
            "Only the player who started this deposit can continue.",
            show_alert=True,
        )
        return DEPOSIT_SETUP_ACK

    try:
        attempt_id = int(data.split(":", 1)[1])
    except (ValueError, IndexError):
        return ConversationHandler.END

    chat_id = context.chat_data.get("deposit_chat_id") or (
        query.message.chat.id if query.message else None
    )
    attempt = get_pending_bind_attempt(attempt_id)
    if (
        attempt is None
        or chat_id is None
        or int(attempt.telegram_chat_id) != int(chat_id)
    ):
        await query.answer()
        if query.message:
            try:
                await query.edit_message_text(
                    "This setup expired. Use /deposit to start again."
                )
            except Exception:
                pass
        _cleanup(context)
        return ConversationHandler.END

    await query.answer()
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    response_data = context.chat_data.get("deposit_setup_response_data") or {}
    if not query.message:
        _cleanup(context)
        return ConversationHandler.END

    await _send_first_time_payment_destination(
        query.message.chat,
        int(chat_id),
        response_data=response_data,
    )
    _record_funnel_from_context(
        context,
        STEP_BIND_SETUP_COMPLETED,
        metadata={"bind_attempt_id": attempt_id},
    )
    return await _complete_deposit_flow(query.message.chat, context)


async def deposit_sub_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        return ConversationHandler.END
    query = update.callback_query
    if await handle_stale_flow_callback(
        update,
        context,
        flow="deposit",
        handler="deposit_sub_chosen",
        cleanup=_cleanup,
    ):
        return ConversationHandler.END
    await query.answer()

    data = query.data or ""
    if not data.startswith("depsub:"):
        return ConversationHandler.END

    sub_id = int(data.split(":")[1])
    sub = get_sub_option_by_id(sub_id)
    if not sub:
        await query.edit_message_text("That option is no longer available.")
        _cleanup(context)
        return ConversationHandler.END

    amount = context.chat_data.get("deposit_amount", "?")
    method_name = context.chat_data.get("deposit_method_name", "")
    display = f"{method_name} — {sub['name']}"

    parent_method_id = context.chat_data.get("deposit_method_id")
    parent = get_method_by_id(int(parent_method_id)) if parent_method_id else None
    parent_slug = (parent.get("slug") or "") if parent else ""
    tier = (
        get_tier_for_amount(int(parent_method_id), amount)
        if parent_method_id and isinstance(amount, Decimal)
        else None
    )
    response_data = _with_method_checkout_settings(sub, parent, tier=tier)
    sub_slug = (sub.get("slug") or "").strip().lower()
    logger.info(
        "deposit_sub_chosen chat_id=%s sub_id=%s sub_slug=%r parent_slug=%r group_checkout=%s",
        query.message.chat.id if query.message else None,
        sub_id,
        sub_slug,
        parent_slug,
        _stripe_checkout_enabled(response_data),
    )
    _record_funnel_from_context(
        context,
        STEP_METHOD_CHOSEN,
        method_slug=parent_slug or sub_slug,
        metadata={"sub_option_id": sub_id, "sub_slug": sub_slug},
    )

    ok = await _send_deposit_method_response(
        query,
        context,
        amount=amount,
        display_name=display,
        method_id=int(parent_method_id) if parent_method_id else None,
        method_slug=parent_slug or sub_slug,
        response_data=response_data,
    )
    if not ok:
        return ConversationHandler.END
    method_id = parent_method_id
    if method_id and isinstance(amount, Decimal):
        try:
            record_method_deposit(method_id, amount)
        except Exception:
            pass
    return await _complete_deposit_flow(query.message.chat, context)


async def _finish_simple_deposit(message, context):
    """Handle simple-mode deposit: send response, record, bonus message."""
    simple = context.chat_data.get("deposit_simple_data")
    club_id = context.chat_data.get("deposit_club_id")
    user_id = context.chat_data.get("deposit_user_id")
    chat_id = context.chat_data.get("deposit_chat_id")

    if simple:
        await _send_simple_response(message, simple)

    _record_funnel_from_context(context, STEP_INSTRUCTIONS_SENT)

    try:
        record_activity(club_id, user_id, chat_id, "deposit")
    except Exception:
        pass

    first = context.chat_data.get("deposit_is_first", False)
    settings = context.chat_data.get("deposit_fd_settings")
    if first and settings and settings["bonus_enabled"] and settings["bonus_pct"] > 0:
        try:
            await message.reply_text(
                f"Since this is your first deposit, you will get a "
                f"{settings['bonus_pct']}% first deposit bonus on us!\n\n"
                f"This must be your first time depositing in the club to be eligible for this bonus."
            )
        except Exception:
            pass

    _schedule_deposit_reminder(context, club_id, chat_id, user_id=user_id)
    _cleanup(context)
    return ConversationHandler.END


async def _send_union_chips_message(chat, context) -> None:
    label = context.chat_data.get("deposit_union_label")
    if not label:
        return
    try:
        await chat.send_message(f"Your chips will be added to {label}!")
    except Exception:
        pass


async def _maybe_rename_group_for_union(context: ContextTypes.DEFAULT_TYPE) -> None:
    shorthand = context.chat_data.get("deposit_union_shorthand")
    chat_id = context.chat_data.get("deposit_chat_id")
    club_id = context.chat_data.get("deposit_club_id")
    if not shorthand or not chat_id or not club_id:
        return

    current_title = None
    try:
        chat = await context.bot.get_chat(int(chat_id))
        current_title = chat.title
    except Exception:
        logger.warning(
            "deposit union rename: could not fetch chat title chat_id=%s",
            chat_id,
            exc_info=True,
        )
        return

    new_title = merge_union_prefix(current_title, str(shorthand))
    if not new_title:
        return

    ok = await rename_support_group_title(
        int(chat_id),
        int(club_id),
        new_title,
        bot=context.bot,
        current_title=current_title,
    )
    if not ok:
        logger.warning(
            "deposit union rename failed chat_id=%s new_title=%r",
            chat_id,
            new_title,
        )


async def _complete_deposit_flow(chat, context: ContextTypes.DEFAULT_TYPE):
    club_id = context.chat_data.get("deposit_club_id")
    chat_id = context.chat_data.get("deposit_chat_id")
    customer_uid = context.chat_data.get("deposit_user_id")
    method_id = context.chat_data.get("deposit_method_id")
    method_slug = None
    if method_id:
        method = get_method_by_id(int(method_id))
        if method:
            method_slug = (method.get("slug") or "").strip().lower() or None
    _record_funnel_from_context(
        context,
        STEP_INSTRUCTIONS_SENT,
        method_slug=method_slug,
    )
    _record_deposit(context)
    _persist_deposit_union(context)
    await _send_union_chips_message(chat, context)
    await _maybe_rename_group_for_union(context)
    await _send_bonus_message(chat, context)
    _schedule_deposit_reminder(context, club_id, chat_id, user_id=customer_uid)
    _cleanup(context)
    return ConversationHandler.END


async def _send_bonus_message(chat, context):
    """If this is a first deposit and bonus is enabled, send the bonus announcement."""
    first = context.chat_data.get("deposit_is_first", False)
    settings = context.chat_data.get("deposit_fd_settings")
    if not first or not settings or not settings["bonus_enabled"] or settings["bonus_pct"] <= 0:
        return
    amount = context.chat_data.get("deposit_amount")
    if not isinstance(amount, Decimal):
        return
    pct = settings["bonus_pct"]
    bonus = (amount * pct / 100).quantize(Decimal("0.01"))
    cap = settings.get("bonus_cap")
    if cap is not None and bonus > cap:
        bonus = cap.quantize(Decimal("0.01"))
    try:
        await chat.send_message(
            f"Since this is your first deposit, you will get a "
            f"{pct}% first deposit bonus of ${bonus:,.2f} added to "
            f"your deposit of ${amount:,.2f} on us!\n\n"
            f"This must be your first time depositing in the club to be eligible for this bonus."
        )
    except Exception:
        pass


def _record_deposit(context):
    club_id = context.chat_data.get("deposit_club_id")
    user_id = context.chat_data.get("deposit_user_id")
    chat_id = context.chat_data.get("deposit_chat_id")
    if club_id and user_id and chat_id:
        try:
            record_activity(club_id, user_id, chat_id, "deposit")
        except Exception:
            pass


def _persist_deposit_union(context):
    """Save the customer's RT/AT union choice on the group for auto chip-add routing."""
    shorthand = context.chat_data.get("deposit_union_shorthand")
    chat_id = context.chat_data.get("deposit_chat_id")
    if shorthand and chat_id:
        try:
            set_last_deposit_union(int(chat_id), str(shorthand))
        except Exception:
            logger.warning(
                "deposit: failed to persist union choice chat_id=%s", chat_id,
                exc_info=True,
            )


async def _send_deposit_method_response(
    query,
    context,
    *,
    amount,
    display_name: str,
    method_id: int | None,
    method_slug: str,
    response_data: dict,
    method: dict | None = None,
    tier: dict | None = None,
) -> bool:
    """When dashboard group Stripe checkout is enabled, create a per-group link from response text.

    Returns True if the deposit response was delivered, False on hard failure.
    """
    response_data = _apply_hardcoded_stripe_below_100(
        response_data,
        method_slug=method_slug,
        amount=amount,
        method=method,
        tier=tier,
    )
    response_data = _prepare_deposit_response_data(
        response_data,
        method_slug=method_slug,
        method=method,
        tier=tier,
    )
    slug = (method_slug or "").strip().lower()
    use_stripe_checkout = _stripe_checkout_enabled(response_data)
    if (
        not use_stripe_checkout
        and _response_has_hyperlink_placeholder(response_data)
        and response_data.get("use_group_checkout_link")
    ):
        response_data = {
            **response_data,
            "group_checkout_provider": response_data.get("group_checkout_provider") or "stripe",
        }
        use_stripe_checkout = _stripe_checkout_enabled(response_data)
    chat_id = context.chat_data.get("deposit_chat_id") or (
        query.message.chat.id if query.message else None
    )
    club_id = context.chat_data.get("deposit_club_id")

    logger.info(
        "deposit_send_response chat_id=%s club_id=%s method_id=%s slug=%r group_checkout=%s configured=%s",
        chat_id,
        club_id,
        method_id,
        slug,
        use_stripe_checkout,
        stripe_configured(),
    )

    if use_stripe_checkout and not stripe_configured():
        logger.warning("deposit: stripe checkout requested but STRIPE_SECRET_KEY not set chat_id=%s", chat_id)
        if club_id is not None:
            await _notify_missing_stripe_secret(context, int(club_id))
        await query.edit_message_text(
            "Card checkout is not available right now (Stripe not configured on the bot). "
            "Add STRIPE_SECRET_KEY or STRIPE_TEST_SECRET_KEY to .env and restart run_test_bot.py."
        )
        return False

    if use_stripe_checkout and stripe_configured():
        if club_id is None:
            logger.error("deposit: stripe checkout requested but deposit_club_id missing chat_id=%s", chat_id)
            await query.edit_message_text(
                "This group is not linked to a club. Card checkout cannot be started."
            )
            return False

        try:
            group_title = getattr(query.message.chat, "title", None)
            result = create_stripe_checkout_session(
                telegram_chat_id=int(chat_id),
                club_id=int(club_id),
                payment_method_id=method_id,
                group_title=group_title,
                checkout_min_usd=response_data.get("checkout_min_amount"),
                checkout_max_usd=response_data.get("checkout_max_amount"),
                checkout_preset_usd=amount if isinstance(amount, Decimal) else None,
            )
            _reset_deposit_info_messages(int(chat_id))
            await query.edit_message_text(f"Deposit via {display_name}")
            if query.message:
                _track_deposit_info_message(int(chat_id), query.message.message_id)

            payload = _build_stripe_response_payload(response_data, result.checkout_url)
            plain_field = payload.pop("_stripe_plain_field", "response_text")
            plain_fallback = payload.pop("_stripe_plain_fallback", None)
            link_only_html = payload.pop("_stripe_link_only_html", None)
            link_only_plain = payload.pop("_stripe_link_only_plain", None)
            chat = query.message.chat
            try:
                _track_deposit_info_messages(
                    int(chat_id),
                    await send_response_messages(chat, payload),
                )
                if link_only_html:
                    await _deposit_send_message(
                        chat,
                        int(chat_id),
                        text=link_only_html,
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
            except Exception:
                logger.warning(
                    "deposit: HTML templated response failed, retrying plain link chat_id=%s",
                    chat_id,
                    exc_info=True,
                )
                fallback = dict(response_data)
                if plain_fallback is not None:
                    fallback[plain_field] = plain_fallback
                    fallback["parse_mode"] = None
                    fallback["disable_web_page_preview"] = True
                    _track_deposit_info_messages(
                        int(chat_id),
                        await send_response_messages(chat, fallback),
                    )
                elif link_only_plain:
                    _track_deposit_info_messages(
                        int(chat_id),
                        await send_response_messages(chat, payload),
                    )
                    await _deposit_send_message(chat, int(chat_id), text=link_only_plain)
            logger.info(
                "deposit: stripe checkout sent chat_id=%s session_id=%s customer_id=%s",
                chat_id,
                result.session_id,
                result.customer_id,
            )
            return True
        except Exception as e:
            err_detail = _stripe_error_detail(e)
            logger.exception(
                "deposit: stripe checkout failed chat_id=%s club_id=%s method_id=%s slug=%r "
                "amount=%r checkout_min_usd=%r checkout_max_usd=%r display_name=%r: %s",
                chat_id,
                club_id,
                method_id,
                slug,
                amount,
                response_data.get("checkout_min_amount"),
                response_data.get("checkout_max_amount"),
                display_name,
                err_detail,
            )
            if club_id is not None:
                await _notify_stripe_checkout_failure(
                    context,
                    club_id=int(club_id),
                    chat_id=int(chat_id) if chat_id is not None else None,
                    group_title=group_title,
                    method_id=method_id,
                    method_slug=slug,
                    display_name=display_name,
                    amount=amount,
                    checkout_min_usd=response_data.get("checkout_min_amount"),
                    checkout_max_usd=response_data.get("checkout_max_amount"),
                    error_detail=err_detail,
                )
            await query.edit_message_text(
                "Card checkout failed to start. Please try again in a minute or contact support."
            )
            return False

    if _response_has_hyperlink_placeholder(response_data):
        logger.warning(
            "deposit: {{hyperlink}} in response but Stripe checkout not used "
            "chat_id=%s method_id=%s group_checkout=%s configured=%s",
            chat_id,
            method_id,
            response_data.get("use_group_checkout_link"),
            stripe_configured(),
        )
        if stripe_configured() and club_id is not None:
            await query.edit_message_text(
                "Checkout link could not be generated. Enable Use group specific link "
                "on this tier in the dashboard, set STRIPE_SECRET_KEY on the worker, "
                "and restart the bot."
            )
            return False

    if not _response_data_has_content(response_data):
        logger.error(
            "deposit: empty response after prepare chat_id=%s method_id=%s slug=%r tier_id=%s",
            chat_id,
            method_id,
            slug,
            tier.get("id") if tier else None,
        )
        await query.edit_message_text(
            "This payment method is not configured yet. Please contact support."
        )
        return False

    await _send_response(query, response_data, amount, display_name)
    return True


def _stripe_error_detail(exc: BaseException) -> str:
    """Human-readable Stripe/API error for logs and admin DM."""
    parts: list[str] = [type(exc).__name__]
    msg = str(exc).strip()
    if msg:
        parts.append(msg[:300])
    code = getattr(exc, "code", None)
    if code:
        parts.append(f"code={code}")
    user_msg = getattr(exc, "user_message", None)
    if user_msg:
        um = str(user_msg).strip()
        if um and um not in msg:
            parts.append(f"stripe_message={um[:200]}")
    return " | ".join(parts)[:500]


async def _notify_stripe_checkout_failure(
    context,
    *,
    club_id: int,
    chat_id: int | None,
    group_title: str | None,
    method_id: int | None,
    method_slug: str,
    display_name: str,
    amount,
    checkout_min_usd,
    checkout_max_usd,
    error_detail: str,
) -> None:
    """DM the club owner when Stripe Checkout session creation fails."""
    try:
        with get_db() as session:
            club = session.query(Club).filter(Club.id == int(club_id)).one_or_none()
            if not club:
                logger.warning("deposit: stripe failure notify skipped — club_id=%s not found", club_id)
                return
            admin_id = int(club.telegram_user_id)
            club_name = club.name
    except Exception:
        logger.warning("deposit: stripe failure notify — could not load club_id=%s", club_id, exc_info=True)
        return

    lines = [
        f"[{club_name}] Stripe checkout failed to start",
        "",
    ]
    if group_title:
        lines.append(f"Group: {group_title[:120]}")
    if chat_id is not None:
        lines.append(f"Chat id: {chat_id}")
    if display_name or method_slug:
        lines.append(f"Method: {display_name or method_slug} ({method_slug or 'n/a'})")
    if method_id is not None:
        lines.append(f"Method id: {method_id}")
    if amount is not None and amount != "?":
        lines.append(f"Deposit amount entered: ${amount}")
    if checkout_min_usd is not None or checkout_max_usd is not None:
        lo = f"${checkout_min_usd}" if checkout_min_usd is not None else "—"
        hi = f"${checkout_max_usd}" if checkout_max_usd is not None else "—"
        lines.append(f"Checkout limits (dashboard): {lo} – {hi}")
    lines.extend(["", f"Error: {error_detail}", "", "See worker logs for full traceback."])
    text = "\n".join(lines)[:3900]

    try:
        await context.bot.send_message(chat_id=admin_id, text=text)
        logger.info(
            "deposit: stripe failure DM sent club_id=%s admin_user_id=%s chat_id=%s",
            club_id,
            admin_id,
            chat_id,
        )
    except Exception:
        logger.warning(
            "deposit: stripe failure DM failed club_id=%s admin_user_id=%s (did they /start the bot?)",
            club_id,
            admin_id,
            exc_info=True,
        )


async def _notify_missing_stripe_secret(context, club_id: int) -> None:
    """DM the club owner when Stripe is selected but STRIPE_SECRET_KEY isn't set."""
    try:
        with get_db() as session:
            club = session.query(Club).filter(Club.id == int(club_id)).one_or_none()
            if not club:
                return
            admin_id = int(club.telegram_user_id)
    except Exception:
        return

    # Basic per-process throttle to avoid spamming the same owner.
    key = f"stripe_secret_missing_notified:{club_id}"
    if context.bot_data.get(key):
        return
    context.bot_data[key] = True

    try:
        await context.bot.send_message(
            chat_id=admin_id,
            text=(
                f"[{club.name}] Stripe Checkout is not configured on the bot worker.\n"
                "Stripe deposits are currently using the static dashboard text instead of a unique checkout link.\n\n"
                "Fix: set STRIPE_SECRET_KEY on the worker environment and restart."
            ),
        )
    except Exception:
        return


async def _send_response(query, data, amount, display_name):
    """Edit the keyboard message to the announcement, then send instructions as a new message below."""
    chat_id = query.message.chat.id
    _reset_deposit_info_messages(chat_id)
    announcement = f"Deposit request for ${amount} via {display_name}"
    await query.edit_message_text(announcement)
    _track_deposit_info_message(chat_id, query.message.message_id)
    _track_deposit_info_messages(
        chat_id,
        await send_response_messages(query.message.chat, data),
    )


async def _send_simple_response(message, data):
    """Send the simple-mode response (text or photo) directly."""
    chat_id = message.chat.id
    _reset_deposit_info_messages(chat_id)
    _track_deposit_info_messages(
        chat_id,
        await send_response_messages(message, data),
    )


def _cleanup(context):
    chat_id = context.chat_data.get("deposit_chat_id")
    if chat_id is not None:
        _DEPOSIT_AWAITING_CHATS.discard(int(chat_id))
    reset_flow_callback_messages(context, flow="deposit")
    clear_active_flow(context)
    for key in (
        "deposit_club_id",
        "deposit_chat_id",
        "deposit_user_id",
        "deposit_amount",
        "deposit_method_name",
        "deposit_method_id",
        "deposit_is_first",
        "deposit_fd_settings",
        "deposit_simple_data",
        "deposit_admin_initiated",
        "deposit_admin_user_id",
        "deposit_awaiting_amount",
        "deposit_union_shorthand",
        "deposit_union_label",
        "deposit_setup_attempt_id",
        "deposit_setup_response_data",
        "deposit_session_id",
        "deposit_requires_method_setup",
    ):
        context.chat_data.pop(key, None)


async def deposit_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text("Deposit cancelled.")
    _cleanup(context)
    return ConversationHandler.END


async def deposit_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.chat_data.get("deposit_chat_id")
    club_id = context.chat_data.get("deposit_club_id")
    if chat_id:
        methods = get_deposit_method_names(club_id) if club_id else []
        method_list = ", ".join(methods) if methods else ""

        text = (
            "We didn\u2019t hear back from you so we are canceling your request. "
            "No worries \u2014 whenever you\u2019re ready, just type /deposit to start again!"
        )
        if method_list:
            text += f"\n\nWe offer: {method_list}."
        text += (
            "\n\nDeposits are available 24/7, so feel free to reach out anytime!"
        )

        try:
            await context.bot.send_message(chat_id=chat_id, text=text)
        except Exception:
            pass
    _cleanup(context)


TIMEOUT_SECONDS = 600

_DEPOSIT_CANCEL = CommandHandler("cancel", deposit_cancel)


def get_deposit_handler() -> ConversationHandler:
    # Test bot: per_user=True so the admin testing solo stays in the same conversation key.
    per_user = is_test_bot_worker()
    return ConversationHandler(
        entry_points=[CommandHandler("deposit", deposit_entry)],
        states={
            DEPOSIT_REFERRAL: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, deposit_referral_received
                ),
                _DEPOSIT_CANCEL,
            ],
            DEPOSIT_AMOUNT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & AMOUNT_TEXT,
                    deposit_amount_received,
                ),
                _DEPOSIT_CANCEL,
            ],
            DEPOSIT_UNION: [
                CallbackQueryHandler(
                    deposit_union_chosen, pattern=r"^depunion:(RT|AT)$"
                ),
                _DEPOSIT_CANCEL,
            ],
            DEPOSIT_CHOOSE: [
                CallbackQueryHandler(deposit_method_chosen, pattern=r"^dep:\d+$"),
                _DEPOSIT_CANCEL,
            ],
            DEPOSIT_SUB: [
                CallbackQueryHandler(deposit_sub_chosen, pattern=r"^depsub:\d+$"),
                _DEPOSIT_CANCEL,
            ],
            DEPOSIT_SETUP_ACK: [
                CallbackQueryHandler(deposit_setup_ack, pattern=r"^depft:\d+$"),
                _DEPOSIT_CANCEL,
            ],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, deposit_timeout),
            ],
        },
        fallbacks=[_DEPOSIT_CANCEL],
        conversation_timeout=TIMEOUT_SECONDS,
        name="deposit_conv",
        per_chat=True,
        per_user=per_user,
    )
