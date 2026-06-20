"""Issue report tickets (no auth until frontend is chosen)."""

from __future__ import annotations

from typing import Annotated, List

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from api.schemas import IssueReportAttachmentRead, IssueReportRead
from bot.services.issue_reports import (
    IssueReportFileInput,
    IssueReportValidationError,
    create_issue_report,
    get_issue_report,
    get_issue_report_attachment,
    list_issue_reports,
)
from db.connection import get_db_dependency

# TODO: auth when frontend chosen (shared secret header + reporter identity).

router = APIRouter(prefix="/api/issue-reports", tags=["issue-reports"])


def _report_to_read(report) -> IssueReportRead:
    return IssueReportRead(
        id=report.id,
        title=report.title,
        description=report.description,
        tags=list(report.tags or []),
        status=report.status,
        reporter_name=report.reporter_name,
        reporter_source=report.reporter_source,
        slack_message_ts=report.slack_message_ts,
        created_at=report.created_at,
        updated_at=report.updated_at,
        attachments=[
            IssueReportAttachmentRead.model_validate(att)
            for att in (report.attachments or [])
        ],
    )


async def _read_upload_files(
    screenshots: List[UploadFile] | None,
) -> list[IssueReportFileInput]:
    if not screenshots:
        return []
    files: list[IssueReportFileInput] = []
    for upload in screenshots:
        if upload.filename is None and not upload.content_type:
            continue
        content = await upload.read()
        filename = upload.filename or "screenshot"
        content_type = upload.content_type or "application/octet-stream"
        files.append(
            IssueReportFileInput(
                filename=filename,
                content_type=content_type,
                content=content,
            )
        )
    return files


@router.post("", response_model=IssueReportRead, status_code=201)
async def post_issue_report(
    title: Annotated[str, Form()],
    description: Annotated[str, Form()],
    tags: Annotated[List[str], Form()] = [],
    reporter_name: Annotated[str | None, Form()] = None,
    screenshots: Annotated[List[UploadFile], File()] = [],
    db: Session = Depends(get_db_dependency),
):
    try:
        file_inputs = await _read_upload_files(screenshots)
        report = await create_issue_report(
            db,
            title=title,
            description=description,
            tags=tags,
            reporter_name=reporter_name,
            reporter_source="api",
            files=file_inputs,
        )
    except IssueReportValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _report_to_read(report)


@router.get("", response_model=List[IssueReportRead])
def get_issue_reports(
    limit: int = 50,
    db: Session = Depends(get_db_dependency),
):
    if limit < 1 or limit > 200:
        raise HTTPException(400, "limit must be between 1 and 200")
    reports = list_issue_reports(db, limit=limit)
    return [_report_to_read(r) for r in reports]


@router.get("/{report_id}", response_model=IssueReportRead)
def get_issue_report_detail(
    report_id: int,
    db: Session = Depends(get_db_dependency),
):
    report = get_issue_report(db, report_id)
    if not report:
        raise HTTPException(404, "Issue report not found")
    return _report_to_read(report)


@router.get("/{report_id}/attachments/{attachment_id}")
def get_issue_report_attachment_bytes(
    report_id: int,
    attachment_id: int,
    db: Session = Depends(get_db_dependency),
):
    attachment = get_issue_report_attachment(db, report_id, attachment_id)
    if not attachment:
        raise HTTPException(404, "Attachment not found")
    return Response(
        content=bytes(attachment.content),
        media_type=attachment.content_type,
        headers={
            "Content-Disposition": f'inline; filename="{attachment.filename}"',
        },
    )
