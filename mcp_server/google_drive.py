"""Upload exports to Google Drive as native Sheets and share them by link.

Uses the Drive v3 REST API through httpx with an OAuth refresh token for the
operator's own Google account (scope ``drive.file``: only files and folders this
app creates). Configure once with ``scripts/google_drive_auth.py``; see
``docs/MCP.md`` -> Google Drive exports.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
FILES_URL = "https://www.googleapis.com/drive/v3/files"
SCOPE = "https://www.googleapis.com/auth/drive.file"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"
FOLDER_MIME = "application/vnd.google-apps.folder"
CSV_MIME = "text/csv"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
HTTP_TIMEOUT_SECONDS = 20.0

ENV_VARS = (
    "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "GOOGLE_OAUTH_REFRESH_TOKEN",
)

# Tests replace this to inject an ``httpx.MockTransport``.
client_factory: Callable[[], httpx.Client] = lambda: httpx.Client(  # noqa: E731
    timeout=HTTP_TIMEOUT_SECONDS
)

_token_lock = threading.Lock()
_token_cache: dict[str, Any] = {}


@dataclass(frozen=True)
class DriveSettings:
    client_id: str
    client_secret: str
    refresh_token: str
    folder_id: str | None


def load_drive_settings() -> DriveSettings:
    values = {name: (os.getenv(name) or "").strip() for name in ENV_VARS}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ToolError(
            "Google Drive export is not configured on the server: set "
            + ", ".join(missing)
            + " (run scripts/google_drive_auth.py; see docs/MCP.md, Google Drive "
            "exports)."
        )
    folder = (os.getenv("GDRIVE_EXPORT_FOLDER_ID") or "").strip() or None
    return DriveSettings(
        client_id=values["GOOGLE_OAUTH_CLIENT_ID"],
        client_secret=values["GOOGLE_OAUTH_CLIENT_SECRET"],
        refresh_token=values["GOOGLE_OAUTH_REFRESH_TOKEN"],
        folder_id=folder,
    )


def reset_token_cache() -> None:
    with _token_lock:
        _token_cache.clear()


def _raise_for_google(response: httpx.Response, action: str) -> None:
    if response.status_code < 400:
        return
    try:
        body = response.json()
    except ValueError:
        body = {}
    error = body.get("error")
    if isinstance(error, dict):
        detail = error.get("message") or error.get("status") or ""
    else:
        detail = body.get("error_description") or error or response.text[:200]
    if error == "invalid_grant":
        raise ToolError(
            "Google rejected the stored refresh token (revoked or expired). Re-run "
            "scripts/google_drive_auth.py and update GOOGLE_OAUTH_REFRESH_TOKEN."
        )
    raise ToolError(f"Google Drive {action} failed ({response.status_code}): {detail}")


def access_token(client: httpx.Client, settings: DriveSettings) -> str:
    """Return a cached access token, refreshing it shortly before expiry."""
    with _token_lock:
        token = _token_cache.get("token")
        if token and _token_cache.get("expires_at", 0) > time.time() + 60:
            return str(token)
        response = client.post(
            TOKEN_URL,
            data={
                "client_id": settings.client_id,
                "client_secret": settings.client_secret,
                "refresh_token": settings.refresh_token,
                "grant_type": "refresh_token",
            },
        )
        _raise_for_google(response, "token refresh")
        body = response.json()
        _token_cache["token"] = body["access_token"]
        _token_cache["expires_at"] = time.time() + int(body.get("expires_in", 3600))
        return str(body["access_token"])


def _multipart(
    metadata: dict[str, Any], content: bytes, mime: str
) -> tuple[bytes, str]:
    boundary = f"gg-export-{uuid.uuid4().hex}"
    parts = [
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode(),
        json.dumps(metadata).encode("utf-8"),
        f"\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n".encode(),
        content,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    return b"".join(parts), f"multipart/related; boundary={boundary}"


def upload_sheet(
    name: str,
    content: bytes,
    source_mime: str,
    *,
    settings: DriveSettings | None = None,
    share_anyone: bool = True,
) -> dict[str, Any]:
    """Upload CSV/XLSX bytes as a native Google Sheet and return its link.

    With ``share_anyone`` the file is readable by anyone who has the link.
    """
    settings = settings or load_drive_settings()
    with client_factory() as client:
        token = access_token(client, settings)
        headers = {"Authorization": f"Bearer {token}"}
        metadata: dict[str, Any] = {"name": name, "mimeType": SHEET_MIME}
        if settings.folder_id:
            metadata["parents"] = [settings.folder_id]
        body, content_type = _multipart(metadata, content, source_mime)
        response = client.post(
            UPLOAD_URL,
            params={
                "uploadType": "multipart",
                "fields": "id,name,webViewLink",
                "supportsAllDrives": "true",
            },
            headers={**headers, "Content-Type": content_type},
            content=body,
        )
        _raise_for_google(response, "upload")
        created = response.json()
        file_id = created["id"]
        if share_anyone:
            permission = client.post(
                f"{FILES_URL}/{file_id}/permissions",
                params={"supportsAllDrives": "true"},
                headers=headers,
                json={"type": "anyone", "role": "reader"},
            )
            _raise_for_google(permission, "sharing")
        url = created.get("webViewLink") or (
            f"https://docs.google.com/spreadsheets/d/{file_id}/edit"
        )
        return {
            "file_id": file_id,
            "name": created.get("name", name),
            "url": url,
            "folder_id": settings.folder_id,
            "shared": "anyone with the link (viewer)" if share_anyone else "private",
        }


def create_folder(client: httpx.Client, token: str, name: str) -> str:
    """Create a Drive folder owned by this app (needed with the drive.file scope)."""
    response = client.post(
        FILES_URL,
        params={"fields": "id"},
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name, "mimeType": FOLDER_MIME},
    )
    _raise_for_google(response, "folder creation")
    return str(response.json()["id"])
