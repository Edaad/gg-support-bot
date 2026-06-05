"""Shared helper for sending bot responses that may contain multiple messages."""

from telegram import InputMediaPhoto

SEPARATOR = "\n---\n"


def _split_text(text: str) -> list[str]:
    """Split text on '---' line delimiter, returning non-empty stripped parts."""
    parts = [p.strip() for p in text.split(SEPARATOR)]
    return [p for p in parts if p]


async def send_response_messages(target, data) -> list[int]:
    """Send a configured response as one or more messages.

    Args:
        target: A Message object (replies to it) OR a Chat object (sends new messages).
        data:   Dict with keys response_type, response_text, response_file_id, response_caption.

    Behaviour:
        - Photo responses send photo(s) first, then any response_text as follow-up messages.
        - Text in response_text is split on a line containing only '---' so each
          segment becomes its own Telegram message.
        - The first message is sent as a reply when *target* is a Message; subsequent
          messages are plain sends to the same chat.

    Returns:
        Telegram message ids for every message sent.
    """
    message_ids: list[int] = []
    is_reply = hasattr(target, "reply_text")
    chat = target.chat if is_reply else target
    rtype = data.get("response_type", "text")
    parse_mode = data.get("parse_mode")
    disable_web_page_preview = bool(data.get("disable_web_page_preview", False))

    if rtype == "photo" and data.get("response_file_id"):
        file_ids = [f.strip() for f in data["response_file_id"].split(",") if f.strip()]
        caption = data.get("response_caption") or None
        if len(file_ids) == 1:
            if is_reply:
                sent = await target.reply_photo(photo=file_ids[0], caption=caption)
            else:
                sent = await chat.send_photo(photo=file_ids[0], caption=caption)
            message_ids.append(sent.message_id)
        else:
            media = [
                InputMediaPhoto(media=fid, caption=caption if i == 0 else None)
                for i, fid in enumerate(file_ids)
            ]
            if is_reply:
                sent_group = await target.reply_media_group(media=media)
            else:
                sent_group = await chat.send_media_group(media=media)
            message_ids.extend(msg.message_id for msg in sent_group)
        is_reply = False  # follow-up text goes as plain messages

    text = data.get("response_text") or ""
    if text:
        parts = _split_text(text)
        for part in parts:
            if is_reply:
                sent = await target.reply_text(
                    part,
                    parse_mode=parse_mode,
                    disable_web_page_preview=disable_web_page_preview,
                )
                is_reply = False
            else:
                sent = await chat.send_message(
                    part,
                    parse_mode=parse_mode,
                    disable_web_page_preview=disable_web_page_preview,
                )
            message_ids.append(sent.message_id)
    return message_ids
