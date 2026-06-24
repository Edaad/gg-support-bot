"""Issue report tickets for account managers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from db.models import Club, IssueReport, IssueReportAttachment

logger = logging.getLogger(__name__)

# Legacy API tag allowlist (category aliases).
ISSUE_REPORT_TAGS: frozenset[str] = frozenset(
    {"bot_issue", "cashout", "deposit", "rakeback", "other"}
)

ISSUE_REPORT_CATEGORIES: frozenset[str] = frozenset(
    {"deposit", "cashout", "bot_issue", "rakeback", "other"}
)

NOTIFY_TAGS: frozenset[str] = frozenset({"head_admin", "engineer", "rb_admin"})

DEFAULT_NOTIFY_BY_CATEGORY: dict[str, list[str]] = {
    "deposit": ["head_admin"],
    "cashout": ["head_admin"],
    "bot_issue": ["engineer"],
    "rakeback": ["rb_admin"],
    "other": ["head_admin"],
}

CATEGORY_LABELS: dict[str, str] = {
    "deposit": "Deposit",
    "cashout": "Cashout",
    "bot_issue": "Bot issue",
    "rakeback": "Rakeback",
    "other": "Other",
}

NOTIFY_LABELS: dict[str, str] = {
    "head_admin": "Head admin",
    "engineer": "Engineer",
    "rb_admin": "RB admin",
}

ALLOWED_IMAGE_CONTENT_TYPES: frozenset[str] = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/gif"}
)

MAX_ATTACHMENTS = 5
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
REMINDER_INTERVAL_HOURS = 2
ATTACHMENT_TYPE_EVIDENCE = "evidence"
ATTACHMENT_TYPE_RESOLUTION = "resolution"


class IssueReportValidationError(ValueError):
    """Invalid issue report input."""


@dataclass(frozen=True)
class IssueReportFileInput:
    filename: str
    content_type: str
    content: bytes


@dataclass(frozen=True)
class ResolveReportResult:
    report_id: int
    title: str
    already_resolved: bool


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def club_label_for_id(session: Session, club_id: int) -> str:
    club = session.query(Club).filter(Club.id == club_id).first()
    return club.name if club else f"club#{club_id}"


def default_notify_for_category(category: str) -> list[str]:
    return list(DEFAULT_NOTIFY_BY_CATEGORY.get(category, ["head_admin"]))


def normalize_tags(raw_tags: list[str] | None) -> list[str]:
    if not raw_tags:
        return []
    parsed: list[str] = []
    for item in raw_tags:
        for part in item.split(","):
            tag = part.strip().lower()
            if tag:
                parsed.append(tag)
    deduped: list[str] = []
    seen: set[str] = set()
    for tag in parsed:
        if tag not in seen:
            seen.add(tag)
            deduped.append(tag)
    invalid = [t for t in deduped if t not in ISSUE_REPORT_TAGS]
    if invalid:
        allowed = ", ".join(sorted(ISSUE_REPORT_TAGS))
        raise IssueReportValidationError(
            f"Invalid tag(s): {', '.join(invalid)}. Allowed: {allowed}"
        )
    return deduped


def normalize_category(raw: str | None) -> str:
    category = (raw or "").strip().lower()
    if category not in ISSUE_REPORT_CATEGORIES:
        allowed = ", ".join(sorted(ISSUE_REPORT_CATEGORIES))
        raise IssueReportValidationError(
            f"Invalid category {raw!r}. Allowed: {allowed}"
        )
    return category


def normalize_notify_tags(raw_tags: list[str] | None) -> list[str]:
    if not raw_tags:
        raise IssueReportValidationError("At least one notify tag is required")
    deduped: list[str] = []
    seen: set[str] = set()
    for tag in raw_tags:
        key = tag.strip().lower()
        if key and key not in seen:
            seen.add(key)
            deduped.append(key)
    invalid = [t for t in deduped if t not in NOTIFY_TAGS]
    if invalid:
        allowed = ", ".join(sorted(NOTIFY_TAGS))
        raise IssueReportValidationError(
            f"Invalid notify tag(s): {', '.join(invalid)}. Allowed: {allowed}"
        )
    return deduped


def validate_files(files: list[IssueReportFileInput], *, max_count: int | None = None) -> None:
    limit = max_count if max_count is not None else MAX_ATTACHMENTS
    if len(files) > limit:
        raise IssueReportValidationError(f"At most {limit} screenshots allowed")
    for f in files:
        content_type = (f.content_type or "").split(";", 1)[0].strip().lower()
        if content_type not in ALLOWED_IMAGE_CONTENT_TYPES:
            allowed = ", ".join(sorted(ALLOWED_IMAGE_CONTENT_TYPES))
            raise IssueReportValidationError(
                f"Unsupported file type {f.content_type!r} for {f.filename!r}. "
                f"Allowed: {allowed}"
            )
        if len(f.content) > MAX_ATTACHMENT_BYTES:
            raise IssueReportValidationError(
                f"File {f.filename!r} exceeds {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB limit"
            )
        if not f.content:
            raise IssueReportValidationError(f"File {f.filename!r} is empty")


def _category_label(report: IssueReport) -> str:
    cat = report.category or (report.tags[0] if report.tags else None)
    return CATEGORY_LABELS.get(cat or "", cat or "(none)")


def _notify_labels(notify_tags: list[str] | None) -> str:
    if not notify_tags:
        return "(none)"
    return ", ".join(NOTIFY_LABELS.get(t, t) for t in notify_tags)


def format_issue_report_slack_body(report: IssueReport, *, session: Session | None = None) -> str:
    reporter = report.reporter_name or "(unknown)"
    if report.reporter_telegram_user_id:
        reporter = f"{reporter} (tg:{report.reporter_telegram_user_id})"
    lines = [
        "Issue report",
        "",
        f"Ticket: #{report.id}",
        f"Title: {report.title}",
        f"Notify: {_notify_labels(list(report.notify_tags or []))}",
        f"Reporter: {reporter} (source={report.reporter_source})",
    ]
    if report.category:
        lines.insert(5, f"Category: {_category_label(report)}")
    if report.group_title:
        lines.append(f"Group: {report.group_title}")
    if report.telegram_chat_id:
        lines.append(f"Chat ID: {report.telegram_chat_id}")
    if report.club_id and session is not None:
        lines.append(f"Club: {club_label_for_id(session, int(report.club_id))}")
    legacy_tags = list(report.tags or [])
    if legacy_tags and not report.category:
        lines.append(f"Tags: {', '.join(legacy_tags)}")
    lines.extend(["", "Details:", report.description])
    return "\n".join(lines)


def _age_label(dt: datetime | None) -> str:
    if dt is None:
        return "?"
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    hours = int(delta.total_seconds() // 3600)
    if hours < 1:
        return f"{int(delta.total_seconds() // 60)}m"
    if hours < 48:
        return f"{hours}h"
    return f"{hours // 24}d"


def format_open_reports_list(reports: list[IssueReport]) -> str:
    if not reports:
        return "No open issue reports."
    lines = ["Open issue reports:", ""]
    for r in reports:
        group = r.group_title or "(no group)"
        lines.append(
            f"#{r.id} — {r.title}\n"
            f"  {_category_label(r)} | {group} | {_age_label(r.created_at)} ago"
        )
    lines.append("\nUse /reports ID for detail.")
    return "\n".join(lines)


def format_resolved_reports_list(reports: list[IssueReport]) -> str:
    if not reports:
        return "No resolved issue reports in the last 30 days."
    lines = ["Resolved issue reports (last 30 days):", ""]
    for r in reports:
        group = r.group_title or "(no group)"
        lines.append(
            f"#{r.id} — {r.title}\n"
            f"  {_category_label(r)} | {group} | resolved {_age_label(r.resolved_at)} ago"
        )
    lines.append("\nUse /reports ID for detail.")
    return "\n".join(lines)


def format_report_detail(report: IssueReport, *, session: Session | None = None) -> str:
    status = report.status or "open"
    lines = [
        f"Report #{report.id} ({status})",
        f"Title: {report.title}",
        f"Category: {_category_label(report)}",
        f"Notify: {_notify_labels(list(report.notify_tags or []))}",
    ]
    if report.group_title:
        lines.append(f"Group: {report.group_title}")
    if report.club_id and session is not None:
        lines.append(f"Club: {club_label_for_id(session, int(report.club_id))}")
    evidence_count = sum(
        1
        for a in (report.attachments or [])
        if (a.attachment_type or ATTACHMENT_TYPE_EVIDENCE) == ATTACHMENT_TYPE_EVIDENCE
    )
    resolution_count = sum(
        1
        for a in (report.attachments or [])
        if a.attachment_type == ATTACHMENT_TYPE_RESOLUTION
    )
    lines.extend(
        [
            f"Evidence: {evidence_count} file(s)",
            "",
            "Details:",
            report.description,
        ]
    )
    if status == "resolved" and report.resolution_notes:
        lines.extend(
            [
                "",
                "Resolution:",
                report.resolution_notes,
            ]
        )
        if resolution_count:
            lines.append(f"Resolution screenshots: {resolution_count} file(s)")
    return "\n".join(lines)


def format_resolve_result(result: ResolveReportResult) -> str:
    if result.already_resolved:
        return f"Report #{result.report_id} was already resolved."
    return f"Report #{result.report_id} resolved — {result.title}"


def list_issue_reports(db: Session, *, limit: int = 50) -> list[IssueReport]:
    return (
        db.query(IssueReport)
        .order_by(IssueReport.created_at.desc())
        .limit(limit)
        .all()
    )


def list_open_reports(db: Session, *, limit: int = 30) -> list[IssueReport]:
    return (
        db.query(IssueReport)
        .filter(IssueReport.status == "open")
        .order_by(IssueReport.created_at.desc())
        .limit(limit)
        .all()
    )


def list_open_reports_needing_reminder(
    db: Session, *, interval_hours: int = REMINDER_INTERVAL_HOURS
) -> list[IssueReport]:
    now = datetime.now(timezone.utc)
    threshold = timedelta(hours=interval_hours)
    reports = (
        db.query(IssueReport)
        .filter(IssueReport.status == "open")
        .order_by(IssueReport.created_at.asc())
        .all()
    )
    due: list[IssueReport] = []
    for report in reports:
        anchor = report.last_slack_reminder_at or report.created_at
        if anchor is None:
            continue
        if now - _as_utc(anchor) >= threshold:
            due.append(report)
    return due


def format_reminder_slack_body(report: IssueReport) -> str:
    age = _age_label(report.created_at)
    lines = [
        "Unresolved incident reminder",
        "",
        f"Ticket: #{report.id}",
        f"Title: {report.title}",
        f"Notify: {_notify_labels(list(report.notify_tags or []))}",
        f"Open for: {age}",
    ]
    if report.category:
        lines.insert(5, f"Category: {_category_label(report)}")
    if report.group_title:
        lines.append(f"Group: {report.group_title}")
    lines.extend(["", "Details:", report.description])
    return "\n".join(lines)


def format_resolution_slack_body(report: IssueReport) -> str:
    lines = [
        "Incident resolved",
        "",
        f"Ticket: #{report.id}",
        f"Title: {report.title}",
        "",
        "Resolution:",
        report.resolution_notes or "(no notes)",
    ]
    return "\n".join(lines)


def list_resolved_reports(
    db: Session, *, limit: int = 30, since_days: int = 30
) -> list[IssueReport]:
    since = datetime.now(timezone.utc) - timedelta(days=since_days)
    return (
        db.query(IssueReport)
        .filter(
            IssueReport.status == "resolved",
            IssueReport.resolved_at >= since,
        )
        .order_by(IssueReport.resolved_at.desc())
        .limit(limit)
        .all()
    )


def get_issue_report(db: Session, report_id: int) -> IssueReport | None:
    return db.query(IssueReport).filter(IssueReport.id == report_id).first()


def get_issue_report_attachment(
    db: Session, report_id: int, attachment_id: int
) -> IssueReportAttachment | None:
    return (
        db.query(IssueReportAttachment)
        .filter(
            IssueReportAttachment.issue_report_id == report_id,
            IssueReportAttachment.id == attachment_id,
        )
        .first()
    )


async def create_issue_report(
    db: Session,
    *,
    title: str,
    description: str,
    tags: list[str] | None = None,
    category: str | None = None,
    notify_tags: list[str] | None = None,
    reporter_name: str | None = None,
    reporter_source: str = "api",
    reporter_telegram_user_id: int | None = None,
    club_id: int | None = None,
    group_title: str | None = None,
    telegram_chat_id: int | None = None,
    files: list[IssueReportFileInput] | None = None,
) -> IssueReport:
    title_clean = (title or "").strip()
    description_clean = (description or "").strip()
    if not title_clean:
        raise IssueReportValidationError("title is required")
    if not description_clean:
        raise IssueReportValidationError("description is required")

    normalized_tags = normalize_tags(tags)
    category_clean: str | None = None
    if category:
        category_clean = normalize_category(category)
    elif normalized_tags:
        category_clean = normalized_tags[0]

    notify_clean: list[str] = []
    if notify_tags is not None:
        notify_clean = normalize_notify_tags(notify_tags)
    elif category_clean:
        notify_clean = default_notify_for_category(category_clean)

    file_inputs = list(files or [])
    validate_files(file_inputs)

    report = IssueReport(
        title=title_clean,
        description=description_clean,
        tags=normalized_tags,
        category=category_clean,
        notify_tags=notify_clean,
        reporter_name=(reporter_name or "").strip() or None,
        reporter_source=(reporter_source or "api").strip() or "api",
        reporter_telegram_user_id=reporter_telegram_user_id,
        club_id=club_id,
        group_title=(group_title or "").strip() or None,
        telegram_chat_id=telegram_chat_id,
    )
    db.add(report)
    db.flush()

    attachments: list[IssueReportAttachment] = []
    for f in file_inputs:
        content_type = f.content_type.split(";", 1)[0].strip().lower()
        att = IssueReportAttachment(
            issue_report_id=report.id,
            filename=f.filename,
            content_type=content_type,
            content=f.content,
            attachment_type=ATTACHMENT_TYPE_EVIDENCE,
        )
        db.add(att)
        attachments.append(att)
    db.flush()

    from bot.services.slack_ops_notify import notify_slack_issue_report

    slack_body = format_issue_report_slack_body(report, session=db)
    file_bytes = [(a.filename, a.content, a.content_type) for a in attachments]
    ok, message_ts, slack_file_ids = await notify_slack_issue_report(
        slack_body,
        tags=notify_clean,
        file_bytes=file_bytes,
    )
    if message_ts:
        report.slack_message_ts = message_ts
    for att, slack_file_id in zip(attachments, slack_file_ids):
        if slack_file_id:
            att.slack_file_id = slack_file_id
    db.flush()
    db.refresh(report)

    if not ok:
        logger.warning(
            "issue_report: Slack notification failed report_id=%s",
            report.id,
        )

    return report


async def resolve_report(
    db: Session,
    report_id: int,
    *,
    resolved_by_telegram_user_id: int,
    resolution_notes: str,
    resolution_files: list[IssueReportFileInput] | None = None,
) -> ResolveReportResult:
    report = get_issue_report(db, report_id)
    if report is None:
        raise IssueReportValidationError(f"Report #{report_id} not found")
    if report.status == "resolved":
        return ResolveReportResult(
            report_id=report.id,
            title=report.title,
            already_resolved=True,
        )

    notes_clean = (resolution_notes or "").strip()
    if not notes_clean:
        raise IssueReportValidationError("resolution notes are required")

    file_inputs = list(resolution_files or [])
    if file_inputs:
        validate_files(file_inputs)

    report.status = "resolved"
    report.resolution_notes = notes_clean
    report.resolved_at = datetime.now(timezone.utc)
    report.resolved_by_telegram_user_id = resolved_by_telegram_user_id

    resolution_attachments: list[IssueReportAttachment] = []
    for f in file_inputs:
        content_type = f.content_type.split(";", 1)[0].strip().lower()
        att = IssueReportAttachment(
            issue_report_id=report.id,
            filename=f.filename,
            content_type=content_type,
            content=f.content,
            attachment_type=ATTACHMENT_TYPE_RESOLUTION,
        )
        db.add(att)
        resolution_attachments.append(att)
    db.flush()

    from bot.services.slack_ops_notify import (
        notify_slack_issue_report,
        notify_slack_issue_report_thread,
    )

    slack_body = format_resolution_slack_body(report)
    file_bytes = [(a.filename, a.content, a.content_type) for a in resolution_attachments]
    notify_tags = list(report.notify_tags or [])

    if report.slack_message_ts:
        ok = await notify_slack_issue_report_thread(
            slack_body,
            thread_ts=report.slack_message_ts,
            tags=notify_tags,
            file_bytes=file_bytes,
        )
    else:
        ok, _, _ = await notify_slack_issue_report(
            f"Resolved\n\n{slack_body}",
            tags=notify_tags,
            file_bytes=file_bytes,
        )

    if not ok:
        logger.warning(
            "issue_report: Slack resolution notify failed report_id=%s",
            report.id,
        )

    db.refresh(report)
    return ResolveReportResult(
        report_id=report.id,
        title=report.title,
        already_resolved=False,
    )


def update_report_details(
    db: Session,
    report_id: int,
    *,
    description: str,
) -> IssueReport:
    report = get_issue_report(db, report_id)
    if report is None:
        raise IssueReportValidationError(f"Report #{report_id} not found")
    if report.status != "open":
        raise IssueReportValidationError(
            f"Report #{report_id} is resolved; cannot edit details"
        )
    description_clean = (description or "").strip()
    if not description_clean:
        raise IssueReportValidationError("details are required")
    report.description = description_clean
    db.flush()
    db.refresh(report)
    return report


async def add_report_evidence(
    db: Session,
    report_id: int,
    files: list[IssueReportFileInput],
) -> IssueReport:
    report = get_issue_report(db, report_id)
    if report is None:
        raise IssueReportValidationError(f"Report #{report_id} not found")
    existing = len(report.attachments or [])
    validate_files(files, max_count=MAX_ATTACHMENTS - existing)
    for f in files:
        content_type = f.content_type.split(";", 1)[0].strip().lower()
        db.add(
            IssueReportAttachment(
                issue_report_id=report.id,
                filename=f.filename,
                content_type=content_type,
                content=f.content,
                attachment_type=ATTACHMENT_TYPE_EVIDENCE,
            )
        )
    db.flush()
    db.refresh(report)
    return report
