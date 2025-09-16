import os
import re
import uuid
from datetime import datetime
from typing import Dict, Optional, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from starlette.responses import JSONResponse
from supabase import create_client, Client

from ..deps import get_current_user

router = APIRouter(
    prefix="/files",
    tags=["Files"],
)

# Constants: allowed extensions and MIME types
# Keep these in sync with the OpenAPI description.
ALLOWED_FILE_EXTENSIONS = {".pdf", ".docx", ".txt", ".xlsx"}
ALLOWED_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # docx
    "text/plain",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # xlsx
    # Some clients may send generic octet-stream; we allow it and rely on extension validation.
    "application/octet-stream",
}
SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

DEFAULT_MAX_FILE_MB = 20  # fallback if env is missing or invalid
ENV_MAX_FILE_MB = "FILES_MAX_SIZE_MB"
ENV_STORAGE_BUCKET = "STORAGE_BUCKET"
ENV_SUPABASE_URL = "SUPABASE_URL"
ENV_SUPABASE_SERVICE_ROLE_KEY = "SUPABASE_SERVICE_ROLE_KEY"
ENV_SUPABASE_ANON_KEY = "SUPABASE_ANON_KEY"


def _json_error(status_code: int, msg: str) -> JSONResponse:
    """Helper to return a consistent JSON error response body."""
    return JSONResponse(status_code=status_code, content={"detail": msg})


def _get_supabase() -> Client:
    """Create a Supabase client using env configuration."""
    url = os.getenv(ENV_SUPABASE_URL)
    key = os.getenv(ENV_SUPABASE_SERVICE_ROLE_KEY) or os.getenv(ENV_SUPABASE_ANON_KEY)
    if not url or not key:
        # This is a server misconfiguration; return 500 consistently.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Supabase configuration missing. Ensure SUPABASE_URL and SUPABASE_ANON_KEY "
            "(or SUPABASE_SERVICE_ROLE_KEY) are set.",
        )
    return create_client(url, key)


def _max_size_bytes() -> int:
    """Return max file size in bytes determined by env FILES_MAX_SIZE_MB (default 20MB)."""
    try:
        mb = int(os.getenv(ENV_MAX_FILE_MB, str(DEFAULT_MAX_FILE_MB)))
        if mb <= 0:
            mb = DEFAULT_MAX_FILE_MB
    except Exception:
        mb = DEFAULT_MAX_FILE_MB
    return mb * 1024 * 1024


def _get_storage_bucket() -> str:
    """Resolve storage bucket name from environment; default to 'uploads' if unset."""
    bucket = os.getenv(ENV_STORAGE_BUCKET, "").strip()
    return bucket or "uploads"


def _secure_filename(filename: str) -> str:
    """Sanitize filename to prevent path traversal and unsafe characters."""
    base = os.path.basename(filename or "")
    base = base.replace(" ", "_")
    base = SAFE_NAME_RE.sub("_", base)
    # avoid empty names and limit length for safety
    base = base or "file"
    if len(base) > 255:
        base = base[:255]
    return base


def _ext_of(name: str) -> str:
    """Return lowercase extension (including dot)."""
    name_lower = (name or "").lower()
    # trust typical last-dot extension
    _, ext = os.path.splitext(name_lower)
    return ext


def _validate_extension_and_mime(filename: str, content_type: Optional[str]) -> None:
    """Enforce extension and MIME whitelist.

    - extension MUST be in ALLOWED_FILE_EXTENSIONS
    - MIME type SHOULD be in ALLOWED_MIME_TYPES (octet-stream is allowed as generic)
    """
    ext = _ext_of(filename)
    if ext not in ALLOWED_FILE_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_FILE_EXTENSIONS))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file extension. Allowed: {allowed}",
        )
    if content_type and content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported MIME type.",
        )


def _validate_session_ownership(sb: Client, session_id: str, user_id: str) -> None:
    """Verify that the session exists and is owned by the user; raise 403/404 appropriately."""
    try:
        owned = (
            sb.table("sessions")
            .select("id,user_id")
            .eq("id", session_id)
            .eq("user_id", user_id)
            .single()
            .execute()
        )
        if getattr(owned, "data", None):
            return
    except Exception:
        pass

    # Does the session exist at all?
    try:
        any_resp = sb.table("sessions").select("id,user_id").eq("id", session_id).single().execute()
        any_row = getattr(any_resp, "data", None)
    except Exception:
        any_row = None

    if any_row is not None and any_row.get("user_id") != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden: session is not owned by user")
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")


def _shape_file_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize DB file row to API response shape."""
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "storage_path": row.get("storage_path"),
        "type": row.get("mime_type"),
        "size": row.get("size"),
        "session_id": row.get("session_id"),
        "created_at": row.get("created_at"),
    }


# PUBLIC_INTERFACE
@router.get(
    "",
    summary="List files",
    description="List files owned by the current authenticated user. Optionally filter by session_id. Enforces RLS and session ownership.",
)
def list_files(
    session_id: Optional[str] = None,
    user=Depends(get_current_user),
):
    """List files for the current user.

    - If session_id is provided, validates that the session exists and is owned by the current user.
    - Applies Row Level Security (RLS) via Supabase; only files with user_id == auth.uid() are returned.
    - Returns a standardized JSON response: {'items': [...]}.

    Errors:
    - 401: Not authenticated
    - 403/404: Session forbidden/not found (when session_id is provided)
    - 500: Backend failure
    """
    if not user or "id" not in user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    sb = _get_supabase()

    if session_id:
        _validate_session_ownership(sb, session_id, user["id"])

    try:
        query = sb.table("files").select("*").eq("user_id", user["id"]).order("created_at", desc=True)
        if session_id:
            query = query.eq("session_id", session_id)
        resp = query.execute()
        rows = getattr(resp, "data", []) or []
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to list files: {e}")

    return {"items": [_shape_file_row(r) for r in rows]}


# PUBLIC_INTERFACE
@router.post(
    "",
    summary="Upload file",
    description=(
        "Authenticated file upload endpoint accepting multipart/form-data for .pdf, .docx, .txt, .xlsx. "
        "Validates size and type, sanitizes filename, stores in Supabase Storage, and persists metadata in DB. "
        "Optional session_id associates the file to a session if owned by the user."
    ),
)
async def upload_file(
    user=Depends(get_current_user),
    file: UploadFile = File(..., description="File to upload (.pdf, .docx, .txt, .xlsx)."),
    session_id: Optional[str] = Form(
        default=None,
        description="Optional session ID this file should be associated with (must belong to current user).",
    ),
):
    """Upload a file to Supabase Storage and persist metadata to the database.

    Security and validation:
    - Auth required via Bearer token.
    - Allowed extensions: .pdf, .docx, .txt, .xlsx
    - Allowed MIME: application/pdf, text/plain, docx/xlsx official, application/octet-stream
    - Max size: FILES_MAX_SIZE_MB (default 20MB) -> returns 413 on overflow.
    - Filenames are sanitized for storage.

    Returns:
    - 200 with JSON metadata on success.
    - 400/401/403/404/413/500 JSON error body with {"detail": "..."}.
    """
    if not user or "id" not in user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    if not file or not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No file provided")

    safe_name = _secure_filename(file.filename)
    _validate_extension_and_mime(safe_name, file.content_type)

    sb = _get_supabase()

    if session_id:
        _validate_session_ownership(sb, session_id, user["id"])

    # Stream read and enforce max size
    max_bytes = _max_size_bytes()
    total = 0
    chunks: list[bytes] = []
    try:
        while True:
            chunk = await file.read(1024 * 1024)  # 1MB
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"File too large. Max {max_bytes // (1024 * 1024)} MB",
                )
            chunks.append(chunk)
    finally:
        try:
            await file.close()
        except Exception:
            pass

    binary = b"".join(chunks)

    # Build storage path: {user_id}/{YYYY-MM-DD}/{uuid}-{filename}
    today = datetime.utcnow().strftime("%Y-%m-%d")
    unique = str(uuid.uuid4())
    object_key = f"{user['id']}/{today}/{unique}-{safe_name}"
    bucket = _get_storage_bucket()

    # Upload
    try:
        try:
            # Best-effort bucket creation; if exists, it will raise, which we ignore.
            sb.storage.create_bucket(bucket)
        except Exception:
            pass

        ct = file.content_type or "application/octet-stream"
        sb.storage.from_(bucket).upload(path=object_key, file=binary, file_options={"contentType": ct})
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Storage upload failed: {e}")

    # Persist metadata
    try:
        payload = {
            "user_id": user["id"],
            "session_id": session_id,
            "name": safe_name,
            "storage_path": f"{bucket}/{object_key}",
            "mime_type": file.content_type,
            "size": total,
        }
        inserted = sb.table("files").insert(payload).select("*").single().execute()
        row = getattr(inserted, "data", None)
        if not row:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to persist file metadata")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to persist file metadata: {e}")

    return JSONResponse(status_code=status.HTTP_200_OK, content=_shape_file_row(row))


# PUBLIC_INTERFACE
@router.delete(
    "/{file_id}",
    summary="Delete file",
    description="Delete a file if owned by the requesting user. Removes object from Supabase Storage and deletes the DB row.",
)
def delete_file(file_id: str, user=Depends(get_current_user)):
    """Delete a file owned by the current user with storage cleanup.

    Behavior:
    - Fetch file row by id with explicit user_id=auth.uid(); if not found, check if exists but not owned (return 403), else 404.
    - Attempt to remove object from Supabase Storage using storage_path ("<bucket>/<key>").
    - Delete DB row (RLS enforces ownership).
    - Return JSON {success: true, id}.

    Errors: 401, 403, 404, 500 with JSON {"detail": "..."}.
    """
    if not user or "id" not in user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    sb = _get_supabase()
    uid = user["id"]

    # Fetch file owned by user
    try:
        owned = sb.table("files").select("*").eq("id", file_id).eq("user_id", uid).single().execute()
        row = getattr(owned, "data", None)
    except Exception:
        row = None

    if not row:
        # does it exist but belong to someone else?
        try:
            any_row_resp = sb.table("files").select("id,user_id").eq("id", file_id).single().execute()
            any_row = getattr(any_row_resp, "data", None)
        except Exception:
            any_row = None

        if any_row is not None and any_row.get("user_id") != uid:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden: file is not owned by user")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    storage_path = (row.get("storage_path") or "").strip()
    if "/" not in storage_path:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Invalid storage path in DB")

    bucket, key = storage_path.split("/", 1)

    # Remove from storage (best-effort)
    try:
        sb.storage.from_(bucket).remove([key])
    except Exception:
        # do not fail delete if storage cleanup fails
        pass

    # Delete DB row
    try:
        sb.table("files").delete().eq("id", file_id).eq("user_id", uid).execute()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to delete file record: {e}")

    return {"success": True, "id": file_id}
