"""Inline keyboard builders for payment bind confirm flows (Telegram JSON dicts)."""

from __future__ import annotations

from bot.services.payment_bind_candidates import METHOD_SHORT, CandidateGroup
from bot.services.player_details import gg_player_id_from_title

try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
except ImportError:  # pragma: no cover - unit tests without PTB
    InlineKeyboardButton = None  # type: ignore
    InlineKeyboardMarkup = None  # type: ignore

MAX_CALLBACK_BYTES = 64


def _cb(*parts: str) -> str:
    data = ":".join(parts)
    if len(data.encode("utf-8")) > MAX_CALLBACK_BYTES:
        raise ValueError(f"callback_data too long ({len(data)} bytes): {data!r}")
    return data


def _button_label(title: str) -> str:
    player_id = gg_player_id_from_title(title)
    if player_id:
        return player_id
    text = (title or "").strip()
    if len(text) <= 32:
        return text
    return text[:29] + "…"


def _short_title(title: str, *, max_len: int = 28) -> str:
    text = (title or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def candidate_picker_markup(
    method_slug: str,
    payment_id: int,
    candidates: list[CandidateGroup],
) -> dict:
    short = METHOD_SHORT[method_slug]
    rows: list[list[dict]] = []
    for candidate in candidates:
        rows.append(
            [
                {
                    "text": _button_label(candidate.group_title),
                    "callback_data": _cb(
                        "pb", "s", short, str(payment_id), str(candidate.telegram_chat_id)
                    ),
                }
            ]
        )
    rows.append(
        [
            {
                "text": "Add another member",
                "callback_data": _cb("pb", "m", short, str(payment_id)),
            },
            {
                "text": "Reset bindings",
                "callback_data": _cb("pb", "rs", short, str(payment_id)),
            },
        ]
    )
    return {"inline_keyboard": rows}


def confirm_bind_markup(
    method_slug: str,
    payment_id: int,
    telegram_chat_id: int,
) -> dict:
    short = METHOD_SHORT[method_slug]
    return {
        "inline_keyboard": [
            [
                {
                    "text": "Confirm",
                    "callback_data": _cb(
                        "pb", "c", short, str(payment_id), str(telegram_chat_id)
                    ),
                },
                {
                    "text": "Back",
                    "callback_data": _cb("pb", "b", short, str(payment_id)),
                },
            ]
        ]
    }


def confirm_reset_markup(method_slug: str, payment_id: int) -> dict:
    short = METHOD_SHORT[method_slug]
    return {
        "inline_keyboard": [
            [
                {
                    "text": "Confirm reset",
                    "callback_data": _cb("pb", "rc", short, str(payment_id)),
                },
                {
                    "text": "Back",
                    "callback_data": _cb("pb", "b", short, str(payment_id)),
                },
            ]
        ]
    }


def reassign_or_add_markup(
    method_slug: str,
    payment_id: int,
    *,
    target_chat_id: int,
    target_title: str,
    show_add: bool = True,
) -> dict:
    short = METHOD_SHORT[method_slug]
    short_name = _short_title(target_title)
    rows: list[list[dict]] = [
        [
            {
                "text": f"Reassign to {short_name}",
                "callback_data": _cb(
                    "pb", "r", short, str(payment_id), str(target_chat_id)
                ),
            }
        ]
    ]
    if show_add:
        rows.append(
            [
                {
                    "text": f"Add {short_name} as possible user",
                    "callback_data": _cb(
                        "pb", "a", short, str(payment_id), str(target_chat_id)
                    ),
                }
            ]
        )
    return {"inline_keyboard": rows}


def confirm_reassign_markup(
    method_slug: str,
    payment_id: int,
    telegram_chat_id: int,
) -> dict:
    return confirm_bind_markup(method_slug, payment_id, telegram_chat_id)


def confirm_add_candidate_markup(
    method_slug: str,
    payment_id: int,
    telegram_chat_id: int,
) -> dict:
    short = METHOD_SHORT[method_slug]
    return {
        "inline_keyboard": [
            [
                {
                    "text": "Confirm add",
                    "callback_data": _cb(
                        "pb", "ac", short, str(payment_id), str(telegram_chat_id)
                    ),
                },
                {
                    "text": "Back",
                    "callback_data": _cb("pb", "b", short, str(payment_id)),
                },
            ]
        ]
    }


def setup_blocked_markup(
    method_slug: str,
    payment_id: int,
    *,
    setup_chat_id: int,
    setup_title: str,
    show_add: bool = True,
) -> dict:
    return reassign_or_add_markup(
        method_slug,
        payment_id,
        target_chat_id=setup_chat_id,
        target_title=setup_title,
        show_add=show_add,
    )


def empty_markup() -> dict:
    return {"inline_keyboard": []}


def to_inline_keyboard(markup: dict):
    """Convert JSON markup dict to python-telegram-bot InlineKeyboardMarkup."""
    if InlineKeyboardMarkup is None:
        raise RuntimeError("python-telegram-bot not installed")
    rows = []
    for row in markup.get("inline_keyboard") or []:
        rows.append(
            [
                InlineKeyboardButton(
                    text=str(button["text"]),
                    callback_data=str(button["callback_data"]),
                )
                for button in row
            ]
        )
    return InlineKeyboardMarkup(rows)
