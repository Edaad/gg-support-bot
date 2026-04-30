"""International phone normalization for Telethon SendCode (dashboard login + `/gc`)."""

PHONE_INVALID_REPLY = (
    "Telegram says that phone number is invalid.\n\n"
    "Use international format: a leading + then country code and full number "
    "(no spaces). Examples: +14155552671 (US), +447911123456 (UK).\n\n"
    "If you rely on MT_PROTO_PHONE_* in Heroku Config Vars, update it there "
    "and redeploy—or remove it and enter the phone in the Telegram login UI."
)


def normalize_phone_for_mtproto(raw: str) -> str:
    """Normalize for Telethon ``SendCode``: ``+<digits>`` after stripping spaces/separators."""
    stripped = "".join(
        ch for ch in (raw or "").strip() if ch not in " \t\n\r-().[]/"
    )
    digits_all = "".join(ch for ch in stripped if ch.isdigit())
    if not digits_all:
        return ""

    if digits_all.startswith("00") and len(digits_all) >= 10:
        digits_all = digits_all[2:]
    return f"+{digits_all}"


def phone_e164_digit_count(plus_phone: str) -> int:
    return len(plus_phone) - 1 if plus_phone.startswith("+") else 0


def phone_len_bounds_ok(plus_phone: str) -> bool:
    n = phone_e164_digit_count(plus_phone)
    return bool(plus_phone) and 8 <= n <= 15
