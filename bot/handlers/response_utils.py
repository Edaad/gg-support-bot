"""Shared helper for sending bot responses that may contain multiple messages."""

from telegram import InputMediaPhoto

SEPARATOR = "\n---\n"


def _split_text(text: str) -> list[str]:
    """Split text on '---' line delimiter, returning non-empty stripped parts."""
    parts = [p.strip() for p in text.split(SEPARATOR)]
    return [p for p in parts if p]


async def send_response_messages(target, data):
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
    """
    is_reply = hasattr(target, "reply_text")
    chat = target.chat if is_reply else target
    rtype = data.get("response_type", "text")

    if rtype == "photo" and data.get("response_file_id"):
        file_ids = [f.strip() for f in data["response_file_id"].split(",") if f.strip()]
        caption = data.get("response_caption") or None
        if len(file_ids) == 1:
            if is_reply:
                await target.reply_photo(photo=file_ids[0], caption=caption)
            else:
                await chat.send_photo(photo=file_ids[0], caption=caption)
        else:
            media = [
                InputMediaPhoto(media=fid, caption=caption if i == 0 else None)
                for i, fid in enumerate(file_ids)
            ]
            if is_reply:
                await target.reply_media_group(media=media)
            else:
                await chat.send_media_group(media=media)
        is_reply = False  # follow-up text goes as plain messages

    text = data.get("response_text") or ""
    if text:
        parts = _split_text(text)
        for part in parts:
            if is_reply:
                await target.reply_text(part)
                is_reply = False
            else:
                await chat.send_message(part)
