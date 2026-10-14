#!/usr/bin/env python
"""One-time Google Drive setup for the MCP export tools.

Run on your own machine (it opens a browser):

    GOOGLE_OAUTH_CLIENT_ID=... GOOGLE_OAUTH_CLIENT_SECRET=... \\
        python scripts/google_drive_auth.py

It asks Google for consent with the ``drive.file`` scope (only files this app
creates), exchanges the code for a refresh token, creates a "GG Dashboard
exports" folder owned by the app, and prints the ``heroku config:set`` line to
paste. Nothing is written to disk. See docs/MCP.md -> Google Drive exports.
"""

from __future__ import annotations

import http.server
import os
import secrets
import socket
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass

import httpx  # noqa: E402

from mcp_server.google_drive import SCOPE, TOKEN_URL, create_folder  # noqa: E402

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
FOLDER_NAME = "GG Dashboard exports"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_code(port: int, state: str) -> str:
    result: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib name
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if query.get("state", [""])[0] != state:
                result["error"] = "state mismatch"
            elif "error" in query:
                result["error"] = query["error"][0]
            else:
                result["code"] = query.get("code", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"Done. You can close this tab and return to the terminal."
            )
            threading.Thread(target=self.server.shutdown, daemon=True).start()

        def log_message(self, *_args):
            return

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    server.serve_forever()
    if "error" in result or not result.get("code"):
        raise SystemExit(f"Google consent failed: {result.get('error', 'no code')}")
    return result["code"]


def main() -> int:
    client_id = (os.getenv("GOOGLE_OAUTH_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("GOOGLE_OAUTH_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        print(
            "Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET (OAuth client "
            "of type Desktop app) first.",
            file=sys.stderr,
        )
        return 2
    port = _free_port()
    redirect_uri = f"http://127.0.0.1:{port}"
    state = secrets.token_urlsafe(16)
    url = (
        AUTH_URL
        + "?"
        + urllib.parse.urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": SCOPE,
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
            }
        )
    )
    print("Opening Google consent in your browser. If it does not open, visit:\n")
    print(url + "\n")
    webbrowser.open(url)
    code = _wait_for_code(port, state)

    with httpx.Client(timeout=20.0) as client:
        response = client.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if response.status_code >= 400:
            print(f"Token exchange failed: {response.text}", file=sys.stderr)
            return 1
        tokens = response.json()
        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            print(
                "Google returned no refresh token. Remove the app's access at "
                "https://myaccount.google.com/permissions and run this again.",
                file=sys.stderr,
            )
            return 1
        folder_id = (os.getenv("GDRIVE_EXPORT_FOLDER_ID") or "").strip()
        if not folder_id:
            folder_id = create_folder(client, tokens["access_token"], FOLDER_NAME)
            print(f'Created Drive folder "{FOLDER_NAME}" ({folder_id}).\n')

    print("Set these on Heroku (keep them out of chat and the repo):\n")
    print(
        "heroku config:set -a <app> "
        f'GOOGLE_OAUTH_CLIENT_ID="{client_id}" '
        f'GOOGLE_OAUTH_CLIENT_SECRET="{client_secret}" '
        f'GOOGLE_OAUTH_REFRESH_TOKEN="{refresh_token}" '
        f'GDRIVE_EXPORT_FOLDER_ID="{folder_id}"'
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
