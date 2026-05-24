"""
modules/google_integration.py
==============================
Google Drive upload + Google Sheets row upsert.

All functions use a Service Account JSON credential supplied via
GOOGLE_CREDENTIALS_PATH in config.py / environment.

Requires:
    pip install google-api-python-client google-auth

Drive folder structure (created automatically if missing):
    Ash & Empires/
      Videos/
        Singles/
        Series/
          {series_slug}/

Sheet columns (1-indexed, exact order):
    A  Content ID
    B  Topic ID
    C  Episode
    D  Type
    E  Series
    F  Title
    G  Google Drive Link
    H  Instagram Caption
    I  TikTok Caption
    J  YouTube Title
    K  YouTube Description
    L  Hashtags
    M  Generated At
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Lazy Google API imports so missing package gives a clean error on use,
# not at module import time.
# ---------------------------------------------------------------------------

def _get_credentials():
    try:
        from google.oauth2 import service_account
    except ImportError:
        raise ImportError(
            "google-auth is not installed. Run: pip install google-api-python-client google-auth"
        )

    try:
        from config import GOOGLE_CREDENTIALS_PATH
    except ImportError:
        GOOGLE_CREDENTIALS_PATH = os.getenv("GOOGLE_CREDENTIALS_PATH", "")

    cred_path = str(GOOGLE_CREDENTIALS_PATH or "").strip()
    if not cred_path:
        raise ValueError(
            "GOOGLE_CREDENTIALS_PATH is not set. "
            "Point it to your service-account JSON file in config.py or .env."
        )
    if not os.path.isfile(cred_path):
        raise FileNotFoundError(
            f"Service-account credentials not found: {cred_path}"
        )

    SCOPES = [
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/spreadsheets",
    ]
    return service_account.Credentials.from_service_account_file(cred_path, scopes=SCOPES)


def _drive_service():
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=_get_credentials(), cache_discovery=False)


def _sheets_service():
    from googleapiclient.discovery import build
    return build("sheets", "v4", credentials=_get_credentials(), cache_discovery=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SHEET_HEADERS = [
    "Content ID",
    "Topic ID",
    "Episode",
    "Type",
    "Series",
    "Title",
    "Google Drive Link",
    "Instagram Caption",
    "TikTok Caption",
    "YouTube Title",
    "YouTube Description",
    "Hashtags",
    "Generated At",
]

CHANNEL_FOLDER_NAME = "Ash & Empires"


def _slug(text: str) -> str:
    """Convert a string to a safe filesystem / Drive folder slug."""
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")


def _get_or_create_drive_folder(service, name: str, parent_id: str | None) -> str:
    """Return the Drive folder ID for `name` inside `parent_id`, creating if needed."""
    query_parts = [
        f"name = '{name}'",
        "mimeType = 'application/vnd.google-apps.folder'",
        "trashed = false",
    ]
    if parent_id:
        query_parts.append(f"'{parent_id}' in parents")

    resp = service.files().list(
        q=" and ".join(query_parts),
        spaces="drive",
        fields="files(id)",
        pageSize=1,
    ).execute()

    files = resp.get("files", [])
    if files:
        return files[0]["id"]

    # Create it
    meta: dict[str, Any] = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        meta["parents"] = [parent_id]

    folder = service.files().create(body=meta, fields="id").execute()
    return folder["id"]


def _resolve_drive_folder(service, is_series: bool, series_slug: str | None, root_folder_id: str) -> str:
    """
    Return the ID of the target folder for the video file:
        <root>/Ash & Empires/Videos/Singles/
     or <root>/Ash & Empires/Videos/Series/<series_slug>/
    """
    channel_id = _get_or_create_drive_folder(service, CHANNEL_FOLDER_NAME, root_folder_id)
    videos_id  = _get_or_create_drive_folder(service, "Videos", channel_id)

    if is_series and series_slug:
        series_root_id = _get_or_create_drive_folder(service, "Series", videos_id)
        return _get_or_create_drive_folder(service, series_slug, series_root_id)
    else:
        return _get_or_create_drive_folder(service, "Singles", videos_id)


def _find_existing_drive_file(service, content_id: str, folder_id: str) -> str | None:
    """Return file ID if a Drive file with this content_id already exists in folder."""
    q = (
        f"'{folder_id}' in parents and name contains '{content_id}' "
        f"and trashed = false"
    )
    resp = service.files().list(q=q, spaces="drive", fields="files(id)", pageSize=5).execute()
    files = resp.get("files", [])
    return files[0]["id"] if files else None


# ---------------------------------------------------------------------------
# Public: Drive upload
# ---------------------------------------------------------------------------

def upload_to_drive(
    video_path: str,
    content_id: str,
    *,
    is_series: bool = False,
    series_slug: str | None = None,
) -> dict:
    """
    Upload video_path to Google Drive.

    - Uses deterministic content_id in filename for duplicate detection.
    - If a file with the same content_id already exists, updates its content
      (same Drive link preserved).
    - Returns dict: {drive_file_id, drive_url}

    Raises on misconfiguration. Callers should wrap in try/except.
    """
    from googleapiclient.http import MediaFileUpload

    try:
        from config import GOOGLE_DRIVE_ROOT_FOLDER_ID
    except ImportError:
        GOOGLE_DRIVE_ROOT_FOLDER_ID = os.getenv("GOOGLE_DRIVE_ROOT_FOLDER_ID", "")

    root_folder_id = str(GOOGLE_DRIVE_ROOT_FOLDER_ID or "").strip()
    if not root_folder_id:
        raise ValueError(
            "GOOGLE_DRIVE_ROOT_FOLDER_ID is not set. "
            "Provide the root Drive folder ID in config.py or .env."
        )

    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")

    service = _drive_service()
    folder_id = _resolve_drive_folder(service, is_series, series_slug, root_folder_id)

    filename = f"{content_id}.mp4"
    existing_id = _find_existing_drive_file(service, content_id, folder_id)

    media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)

    if existing_id:
        print(f"   📁 Drive: Updating existing file ({existing_id}) for content_id={content_id}")
        updated = service.files().update(
            fileId=existing_id,
            media_body=media,
            fields="id",
        ).execute()
        file_id = updated["id"]
    else:
        print(f"   📁 Drive: Uploading new file for content_id={content_id}")
        file_meta: dict[str, Any] = {
            "name": filename,
            "parents": [folder_id],
            "description": f"content_id={content_id}",
        }
        created = service.files().create(
            body=file_meta,
            media_body=media,
            fields="id",
        ).execute()
        file_id = created["id"]

    # Make file accessible (optional: remove if you want private)
    try:
        service.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
        ).execute()
    except Exception:
        pass  # Not fatal if sharing fails

    drive_url = f"https://drive.google.com/file/d/{file_id}/view"
    print(f"   ✅ Drive upload complete: {drive_url}")
    return {"drive_file_id": file_id, "drive_url": drive_url}


# ---------------------------------------------------------------------------
# Public: Sheets upsert
# ---------------------------------------------------------------------------

def sync_to_sheet(metadata: dict, captions: dict) -> str:
    """
    Add or update one row in the Google Sheet for this content_id.

    - If GOOGLE_SHEET_ID is missing/invalid, raises a clear ValueError.
    - If the header row is missing, writes it first.
    - Uses content_id to find an existing row (upsert).
    - Returns sheet_row_id (string, 1-based row number that was written).
    """
    try:
        from config import GOOGLE_SHEET_ID
    except ImportError:
        GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")

    sheet_id = str(GOOGLE_SHEET_ID or "").strip()
    if not sheet_id:
        raise ValueError(
            "GOOGLE_SHEET_ID is not set. "
            "Set it in config.py or .env to enable Sheet sync."
        )

    service = _sheets_service()
    spreadsheet = service.spreadsheets()

    # Validate sheet is accessible
    try:
        spreadsheet.get(spreadsheetId=sheet_id).execute()
    except Exception as exc:
        raise ValueError(
            f"Google Sheet {sheet_id!r} is not accessible: {exc}. "
            "Check GOOGLE_SHEET_ID and that the service account has Editor access."
        ) from exc

    RANGE = "Sheet1!A:M"

    # Read existing data
    existing = spreadsheet.values().get(
        spreadsheetId=sheet_id, range=RANGE
    ).execute()
    rows = existing.get("values", [])

    # Ensure header row exists
    if not rows or rows[0] != _SHEET_HEADERS:
        spreadsheet.values().update(
            spreadsheetId=sheet_id,
            range="Sheet1!A1",
            valueInputOption="RAW",
            body={"values": [_SHEET_HEADERS]},
        ).execute()
        # Re-read
        existing = spreadsheet.values().get(
            spreadsheetId=sheet_id, range=RANGE
        ).execute()
        rows = existing.get("values", [])

    content_id = str(metadata.get("content_id", ""))

    # Build row values in column order
    now_str = metadata.get("generated_at") or time.strftime("%Y-%m-%dT%H:%M:%S")
    row_values = [
        content_id,
        str(metadata.get("topic_id", "")),
        str(metadata.get("episode", "")),
        str(metadata.get("type", "")),
        str(metadata.get("series", "")),
        str(metadata.get("title", "")),
        str(metadata.get("drive_url", "")),
        str(captions.get("instagram_caption", "")),
        str(captions.get("tiktok_caption", "")),
        str(captions.get("youtube_title", "")),
        str(captions.get("youtube_description", "")),
        str(captions.get("hashtags", "")),
        now_str,
    ]

    # Find existing row by content_id (column A)
    existing_row_index = None  # 0-based in `rows`
    for i, row in enumerate(rows):
        if i == 0:
            continue  # skip header
        if row and row[0] == content_id:
            existing_row_index = i
            break

    if existing_row_index is not None:
        # Update existing row (rows are 1-indexed in Sheets; +1 for header offset)
        sheet_row = existing_row_index + 1  # 1-based
        range_str = f"Sheet1!A{sheet_row}:M{sheet_row}"
        spreadsheet.values().update(
            spreadsheetId=sheet_id,
            range=range_str,
            valueInputOption="RAW",
            body={"values": [row_values]},
        ).execute()
        print(f"   ✅ Sheet row {sheet_row} updated (content_id={content_id})")
        return str(sheet_row)
    else:
        # Append new row
        spreadsheet.values().append(
            spreadsheetId=sheet_id,
            range="Sheet1!A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [row_values]},
        ).execute()
        # Return the new row number
        existing2 = spreadsheet.values().get(
            spreadsheetId=sheet_id, range="Sheet1!A:A"
        ).execute()
        all_a = existing2.get("values", [])
        sheet_row = len(all_a)  # last row
        print(f"   ✅ Sheet row {sheet_row} appended (content_id={content_id})")
        return str(sheet_row)
