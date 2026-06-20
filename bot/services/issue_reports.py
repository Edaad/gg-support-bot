"""Issue report tickets for account managers."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from db.models import IssueReport, IssueReportAttachment

ISSUE_REPORT_TAGS: frozenset[str] = frozenset(
    {"bot_issue", "cashout", "deposit", "rakeback"}
)

ALLOWED_IMAGE_CONTENT_TYPES: frozenset[str] = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/gif"}
)

MAX_ATTACHMENTS = 5
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024


class IssueReportValidationError(ValueError):
    """Invalid issue report input."""


@dataclass(frozen=True)
class IssueReportFileInput:
    filename: str
    content_type: str
    content: bytes


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


def validate_files(files: list[IssueReportFileInput]) -> None:
    if len(files) > MAX_ATTACHMENTS:
        raise IssueReportValidationError(
            f"At most {MAX_ATTACHMENTS} screenshots allowed"
        )
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


def format_issue_report_slack_body(report: IssueReport) -> str:
    reporter = report.reporter_name or "(unknown)"
    tags_label = ", ".join(report.tags) if report.tags else "(none)"
    return "\n".join(
        [
            "Issue report",
            "",
            f"Ticket: #{report.id}",
            f"Title: {report.title}",
            f"Reporter: {reporter} (source={report.reporter_source})",
            f"Tags: {tags_label}",
            "",
            "Description:",
            report.description,
        ]
    )


def list_issue_reports(db: Session, *, limit: int = 50) -> list[IssueReport]:
    return (
        db.query(IssueReport)
        .order_by(IssueReport.created_at.desc())
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
    reporter_name: str | None = None,
    reporter_source: str = "api",
    files: list[IssueReportFileInput] | None = None,
) -> IssueReport:
    title_clean = (title or "").strip()
    description_clean = (description or "").strip()
    if not title_clean:
        raise IssueReportValidationError("title is required")
    if not description_clean:
        raise IssueReportValidationError("description is required")

    normalized_tags = normalize_tags(tags)
    file_inputs = list(files or [])
    validate_files(file_inputs)

    report = IssueReport(
        title=title_clean,
        description=description_clean,
        tags=normalized_tags,
        reporter_name=(reporter_name or "").strip() or None,
        reporter_source=(reporter_source or "api").strip() or "api",
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
        )
        db.add(att)
        attachments.append(att)
    db.flush()

    from bot.services.slack_ops_notify import notify_slack_issue_report

    slack_body = format_issue_report_slack_body(report)
    file_bytes = [(a.filename, a.content, a.content_type) for a in attachments]
    ok, message_ts, slack_file_ids = await notify_slack_issue_report(
        slack_body,
        tags=normalized_tags,
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
        import logging

        logging.getLogger(__name__).warning(
            "issue_report: Slack notification failed report_id=%s",
            report.id,
        )

    return report
