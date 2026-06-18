"""Readable Slack mrkdwn bodies for ops notifications."""

from __future__ import annotations

import re

SOURCE_HEADERS: dict[str, str] = {
    "migration_recovery": ":arrows_counterclockwise: *Migration Recovery*",
    "notification_report": ":bell: *Notification Report*",
    "recovery_membership_audit": ":mag: *Recovery Membership Audit*",
    "recovery_triage": ":clipboard: *Recovery Triage*",
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
    if source == "migration_recovery":
        return _beautify_migration_recovery(body)
    if source == "notification_report":
        return _beautify_notification_report(body)
    if source == "recovery_membership_audit":
        return _beautify_recovery_membership_audit(body)
    if source == "recovery_triage":
        return _beautify_recovery_triage(body)
    return body


def _beautify_migration_recovery(body: str) -> str:
    if body.startswith("Migration recovery progress"):
        return _beautify_migration_summary(body)
    if body.startswith("[Migration recovery ops]") or "FloodWait" in body:
        return _beautify_rate_limit(body)
    if body.startswith("Issue:") or "\nGC:" in body:
        return _beautify_migration_failure(body)
    return body


def _beautify_migration_summary(body: str) -> str:
    lines = body.splitlines()
    out = ["*Progress* (tier 1+2)", ""]
    club_name: str | None = None
    club_lines: list[str] = []

    def flush_club() -> None:
        nonlocal club_name, club_lines
        if club_name:
            out.append(f"*{club_name}*")
            out.extend(club_lines)
            out.append("")
        club_name = None
        club_lines = []

    for raw in lines[1:]:
        line = raw.strip()
        if not line:
            continue
        if not line.startswith(
            (
                "in group:",
                "queue left:",
                "left:",
                "direct added:",
                "in group pending queue:",
                "membership check errors:",
                "tier 1+2 pending:",
                "tier 3 pending:",
                "skipped:",
            )
        ):
            if line == "Queue snapshot (all tiers)":
                flush_club()
                out.append("*Queue snapshot* (all tiers)")
                out.append("")
                continue
            flush_club()
            club_name = line
            continue
        if line.startswith("in group:") or line.startswith("queue left:"):
            club_lines.append(f"• {line.replace(' | ', '  |  ')}")
        elif line.startswith("left:"):
            club_lines.append(f"• {line.replace(' | ', '  |  ')}")
        elif line.startswith("direct added:"):
            club_lines.append(f"• {line}")
        elif line.startswith("in group pending queue:"):
            club_lines.append(f"• {line}")
        elif line.startswith("membership check errors:"):
            club_lines.append(f"• {line}")
        elif line.startswith("tier 1+2 pending:") or line.startswith("skipped:"):
            club_lines.append(f"• {line.replace(' | ', '  |  ')}")
    flush_club()
    return "\n".join(out).rstrip()


def _beautify_recovery_triage(body: str) -> str:
    lines = [ln.rstrip() for ln in body.splitlines()]
    out: list[str] = []
    section = "summary"
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("Migration recovery triage"):
            mode = line.split("—", 1)[-1].strip() if "—" in line else ""
            out.append(f"*Recovery triage* — {mode}" if mode else "*Recovery triage*")
            out.append("")
            continue
        if line == "Queue snapshot (all tiers)":
            out.append("")
            out.append("*Queue snapshot* (all tiers)")
            out.append("")
            section = "queue"
            continue
        if section == "queue":
            if line.startswith("tier ") or line.startswith("skipped:"):
                out.append(f"• {line.replace(' | ', '  |  ')}")
            else:
                out.append(f"*{line}*")
            continue
        if line.startswith("DB apply results:"):
            section = "apply"
            out.append("*Database*")
            continue
        if line.startswith("Output CSV:"):
            out.append(f"• `{line.split(':', 1)[1].strip()}`")
            continue
        if section == "apply" and ":" in line:
            key, val = line.split(":", 1)
            out.append(f"• {key.strip()}: {val.strip()}")
            continue
        if line.startswith("Total rows:"):
            out.append(f"• {line}")
            continue
        if line.startswith("promote:") or line.startswith("repair_pending:"):
            out.append(f"• {line}")
            continue
        if line.startswith("drop_") or line.startswith("unchanged:"):
            out.append(f"• {line}")
            continue
        if ":" in line and (
            line.startswith("round_table:")
            or line.startswith("creator_club:")
            or line.startswith("clubgto:")
        ):
            out.append(f"• {line}")
    return "\n".join(out).rstrip()


def format_recovery_triage_slack(
    *,
    summary_lines: list[str],
    output_csv: str | None = None,
    include_queue_snapshot: bool = True,
) -> str:
    body = "\n".join(summary_lines)
    if output_csv:
        body += f"\n\nOutput CSV: {output_csv}"
    if include_queue_snapshot:
        from bot.services.migration_recovery import (
            fetch_club_recovery_queue_snapshots,
            format_recovery_queue_snapshot,
        )

        snap = format_recovery_queue_snapshot(fetch_club_recovery_queue_snapshots())
        if snap:
            body += "\n\n" + snap
    return _beautify_recovery_triage(body)


def _beautify_rate_limit(body: str) -> str:
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    out = ["*Rate limited — recovery paused*", ""]
    for ln in lines:
        if ln.startswith("[Migration recovery ops]"):
            continue
        if ln.startswith("Telegram rate limit"):
            out.append(f"• {ln}")
        elif ln.startswith("GC:"):
            out.append(f"• Group: `{ln[4:].strip()}`")
        elif ln.startswith("club="):
            out.append(f"• Club: `{ln[5:].strip()}`")
        elif ln.startswith("chat_id="):
            out.append(f"• chat_id: `{ln[8:].strip()}`")
        elif "auto-disabled" in ln.lower():
            out.append("")
            out.append(f"_{ln}_")
        else:
            out.append(ln)
    return "\n".join(out)


def _beautify_migration_failure(body: str) -> str:
    lines = [ln.rstrip() for ln in body.splitlines()]
    issue_kind = ""
    detail_lines: list[str] = []
    group_title = ""
    chat_id = ""
    club = ""

    i = 0
    if lines and lines[0].startswith("Issue:"):
        issue_kind = lines[0].split(":", 1)[1].strip()
        i = 1
    while i < len(lines):
        ln = lines[i].strip()
        if not ln:
            i += 1
            continue
        if ln.startswith("GC:"):
            group_title = ln[3:].strip()
        elif ln.startswith("chat_id="):
            chat_id = ln.split("=", 1)[1].strip()
        elif ln.startswith("club="):
            club = ln.split("=", 1)[1].strip()
        elif ln.startswith("Failures:"):
            detail_lines.append(ln)
        elif issue_kind and ln.lower() == issue_kind.lower():
            pass
        else:
            detail_lines.append(ln)
        i += 1

    title = issue_kind.replace("_", " ").title() if issue_kind else "Alert"
    out = [f"*{title}*", ""]
    if group_title:
        out.append(f"*Group:* `{group_title}`")
    meta: list[str] = []
    if club:
        meta.append(f"club: `{club}`")
    if chat_id:
        meta.append(f"chat: `{chat_id}`")
    if meta:
        out.append("  ".join(meta))
    if detail_lines:
        out.append("")
        out.append("*Details:*")
        for dl in detail_lines:
            if dl.startswith("Failures:"):
                failures = dl[len("Failures:") :].strip()
                for part in failures.split(";"):
                    part = part.strip()
                    if not part:
                        continue
                    out.append(f"• `{part}`")
            else:
                out.append(f"• {dl}")
    return "\n".join(out)


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


def _beautify_recovery_membership_audit(body: str) -> str:
    lines = [ln.rstrip() for ln in body.splitlines()]
    out: list[str] = []
    section: str | None = None

    for ln in lines:
        stripped = ln.strip()
        if not stripped:
            continue
        if stripped.startswith("Recovery player membership"):
            out.append("*Tier 1+2 MTProto audit*")
            continue
        if stripped.startswith("Applied player ID"):
            out.append("")
            out.append(f"_{stripped}_")
            continue
        if stripped.startswith("Player in group"):
            section = "presence"
            out.append("")
            out.append("*Player in group* (≥1 eligible human)")
            continue
        if stripped.startswith("DB apply results"):
            section = "apply"
            out.append("")
            out.append("*DB apply*")
            continue
        if stripped.startswith("Source CSV:") or stripped.startswith("Output CSV:"):
            out.append(f"• {stripped}")
            continue
        if section == "presence":
            if stripped.startswith("ALL:"):
                out.append(f"• *{stripped}*")
            else:
                out.append(f"• {stripped}")
        elif section == "apply":
            out.append(f"• {stripped}")
        else:
            out.append(stripped)
    return "\n".join(out)


def format_recovery_membership_audit_slack(
    *,
    summary_lines: list[str],
    source_csv: str | None = None,
    output_csv: str | None = None,
) -> str:
    body = "\n".join(summary_lines)
    if source_csv:
        body += f"\n\nSource CSV: {source_csv}"
    if output_csv:
        body += f"\nOutput CSV: {output_csv}"
    return _beautify_recovery_membership_audit(body)


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
