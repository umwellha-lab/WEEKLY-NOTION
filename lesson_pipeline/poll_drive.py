#!/usr/bin/env python3
"""Google Drive ingestion: split only; never generate content without verified project prompt.

Requires service account editor access to the input folder.
Results remain on private Google Drive, never in the public GitHub repo.
"""
from __future__ import annotations
import json
import os
import tempfile
from pathlib import Path
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from unit_splitter import extract_units

FOLDER_ID = "1_sdFfn1cCPuiJrJ3im0VwD9oZMgYBlqU"
OUTPUT_NAME = "_수업자료자동생성_결과"
SCOPES = ["https://www.googleapis.com/auth/drive"]
MAX_BYTES = 80 * 1024 * 1024


def drive_client():
    raw = os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        raise RuntimeError("GDRIVE_SERVICE_ACCOUNT_JSON absent. No Drive files were touched.")
    info = json.loads(raw)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def children(api, parent, *, name=None, mime=None):
    cond = [f"'{parent}' in parents", "trashed = false"]
    if name is not None:
        cond.append("name = '%s'" % name.replace("'", "\\'"))
    if mime is not None:
        cond.append("mimeType = '%s'" % mime)
    found, token = [], None
    while True:
        result = api.files().list(
            q=" and ".join(cond),
            fields="nextPageToken,files(id,name,mimeType,size,md5Checksum,webViewLink)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            pageSize=100,
            pageToken=token
        ).execute()
        found.extend(result.get("files", []))
        token = result.get("nextPageToken")
        if not token:
            break
    return found


def folder(api, parent, name):
    hits = children(api, parent, name=name, mime="application/vnd.google-apps.folder")
    if len(hits) > 1:
        raise RuntimeError(f"Ambiguous duplicate folder: {name}")
    if hits:
        return hits[0]["id"]
    return api.files().create(
        body={"name": name, "parents": [parent], "mimeType": "application/vnd.google-apps.folder"},
        fields="id", supportsAllDrives=True
    ).execute()["id"]


def upload(api, path, parent):
    suffix = Path(path).suffix.lower()
    if suffix not in (".pdf", ".json"):
        raise ValueError("Only uncompressed PDF/JSON outputs are allowed; ZIP archives are prohibited.")
    mime = "application/pdf" if suffix == ".pdf" else "application/json"
    return api.files().create(
        body={"name": Path(path).name, "parents": [parent]},
        media_body=MediaFileUpload(str(path), mimetype=mime, resumable=True),
        fields="id,webViewLink", supportsAllDrives=True
    ).execute()


def download(api, file_id, target):
    req = api.files().get_media(fileId=file_id, supportsAllDrives=True)
    with open(target, "wb") as out:
        dl = MediaIoBaseDownload(out, req)
        done = False
        while not done:
            _, done = dl.next_chunk()


def run():
    api = drive_client()
    sources = children(api, FOLDER_ID, mime="application/pdf")
    if not sources:
        print("No PDF uploaded; nothing to process")
        return
    outputs = folder(api, FOLDER_ID, OUTPUT_NAME)
    for item in sources:
        if int(item.get("size", 0)) > MAX_BYTES:
            print("MANUAL REVIEW: oversized file", item["id"])
            continue
        checksum = item.get("md5Checksum")
        if not checksum:
            print("MANUAL REVIEW: file checksum absent", item["id"])
            continue
        job = folder(api, outputs, "source_" + item["id"] + "_" + checksum[:12])
        if children(api, job, name="unit_manifest.json"):
            print("Already checked:", item["name"])
            continue
        with tempfile.TemporaryDirectory() as temp:
            original = Path(temp) / "uploaded.pdf"
            download(api, item["id"], original)
            destination = Path(temp) / "units"
            manifest = extract_units(original, destination)
            manifest["drive_source_id"] = item["id"]
            manifest["drive_source_name"] = item["name"]
            manifest["drive_source_url"] = item.get("webViewLink", "")
            manifest["content_generation"] = (
                "BLOCKED until exact ChatGPT project prompt and authorized AI runner are configured"
            )
            if not manifest["requires_review"]:
                for sub in sorted(destination.glob("Unit_*")):
                    for file in sub.glob("*.pdf"):
                        unit_parent = folder(api, job, sub.name)
                        upload(api, file, unit_parent)
                extra = destination / "분할제외페이지_원본보관.pdf"
                if extra.exists():
                    upload(api, extra, job)
            final_manifest = Path(temp) / "unit_manifest.json"
            final_manifest.write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            upload(api, final_manifest, job)
            print(manifest["status"], item["name"], len(manifest["units"]), "Units")


if __name__ == "__main__":
    run()
