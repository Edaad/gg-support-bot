"""Readable Slack mrkdwn bodies for ops notifications."""

from __future__ import annotations

import re

SOURCE_HEADERS: dict[str, str] = {
    "notification_report": ":bell: *Notification Report*",
    "issue_report": ":ticket: *Issue Report*",
    "multi_payer_warning": ":warning: *Multi-Payer Warning*",
}


def slack_header(source: str, *, mention: str | None = None) -> str:
    label = SOURCE_HEADERS.get(source, f"*[{source}]*")
    if mention:
        return f"{mention} {label}"
    return label


def beautify_slack_body(text: str, *, source: str) -> str:
    body = (text or "").strip()
    if not body:
        return body
    if source == "notification_report":
        return _beautify_notification_report(body)
    if source == "issue_report":
        return _beautify_issue_report(body)
    return body


def _beautify_notification_report(body: str) -> str:
    if "Notification bug report" not in body:
        return body

    reporter = _field(body, r"Reporter:\s*(.+)")
    notif_ref = _field_pair(body, r"Notification chat_id=(\S+)\s+message_id=(\S+)")
    reason = _extract_block(body, "Reason:", end_markers=())
    original = _extract_block(body, "Original notification:", ("---", "Reason:"))

    out = ["*Bug report*", ""]
    if reporter:
        out.append(f"*Reporter:* {reporter}")
    if notif_ref:
        chat_id, msg_id = notif_ref
        out.append(f"*Notification:* chat `{chat_id}`  msg `{msg_id}`")
    if reason:
        out.append("")
        out.append(f"*Reason:* {reason.strip()}")
    if original:
        out.append("")
        out.append("*Original notification:*")
        out.append("```")
        out.append(original.strip())
        out.append("```")
    return "\n".join(out)


def _beautify_issue_report(body: str) -> str:
    if "Issue report" not in body and "Unresolved incident reminder" not in body:
        return body

    ticket_id = _field(body, r"Ticket:\s*#(\d+)")
    title = _field(body, r"Title:\s*(.+)")
    notify_raw = _field(body, r"Notify:\s*(.+)")
    group = _field(body, r"Group:\s*(.+)")
    club = _field(body, r"Club:\s*(.+)")
    open_for = _field(body, r"Open for:\s*(.+)")
    details = _extract_block(
        body,
        "Details:",
        (
            "Reporter:",
            "Tags:",
            "Title:",
            "Notify:",
            "Category:",
            "Group:",
            "Club:",
            "Chat ID:",
            "Open for:",
            "Resolution:",
        ),
    )
    description = _extract_block(
        body, "Description:", ("Reporter:", "Tags:", "Title:", "Notify:", "Details:")
    )
    if not details and description:
        details = description

    out: list[str] = []
    if ticket_id:
        out.append(f"*Ticket* `#{ticket_id}`")
    if title:
        out.append(f"*Title:* {title.strip()}")
    if notify_raw and notify_raw.strip() not in ("(none)", ""):
        out.append(f"*For:* {notify_raw.strip()}")
    if open_for:
        out.append(f"*Open for:* {open_for.strip()}")
    if group:
        out.append(f"*Group:* {group.strip()}")
    if club:
        out.append(f"*Club:* {club.strip()}")
    if details:
        out.append("")
        out.append("*Details:*")
        out.append(details.strip())
    return "\n".join(out)


def _field(text: str, pattern: str) -> str | None:
    m = re.search(pattern, text, re.MULTILINE)
    if not m:
        return None
    return m.group(1).strip()


def _field_pair(text: str, pattern: str) -> tuple[str, str] | None:
    m = re.search(pattern, text, re.MULTILINE)
    if not m:
        return None
    return m.group(1).strip(), m.group(2).strip()


def _extract_block(text: str, start_label: str, end_markers: tuple[str, ...]) -> str:
    idx = text.find(start_label)
    if idx < 0:
        return ""
    chunk = text[idx + len(start_label) :].lstrip("\n")
    if start_label == "Original notification:":
        if chunk.startswith("---"):
            chunk = chunk[3:].lstrip("\n")
        end = chunk.find("\n---")
        if end >= 0:
            chunk = chunk[:end]
        return chunk.strip()
    return chunk.strip()
