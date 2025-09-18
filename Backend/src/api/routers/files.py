import os
import time
import uuid
from typing import Dict, Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from supabase import create_client, Client

from ..deps import get_current_user

router = APIRouter(prefix="/files", tags=["Files"])

# Internal helpers
_MAX_SIZE_BYTES = 100 * 1024 * 1024  # 100MB
_ALLOWED_EXTS = {".pdf", ".docx", ".txt", ".xlsx"}
_ALLOWED_MIME_PREFIXES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "text/plain",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # .xlsx
}

def _get_ext(filename: str) -> str:
    filename = filename or ""
    dot = filename.rfind(".")
    if dot == -1:
        return ""
    return filename[dot:].lower()

def _validate_file(upload: UploadFile, size: Optional[int]) -> None:
    ext = _get_ext(upload.filename or "")
    if ext not in _ALLOWED_EXTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file extension: {ext}. Allowed: {', '.join(sorted(_ALLOWED_EXTS))}",
        )

    mime = (upload.content_type or "").lower()
    if not any(mime.startswith(prefix) for prefix in _ALLOWED_MIME_PREFIXES):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported content type: {mime}.",
        )

    if size is not None and size > _MAX_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File too large: {size} bytes. Max is 104857600 bytes (100MB).",
        )

def _get_supabase() -> Client:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        raise RuntimeError(
            "Supabase configuration missing. Ensure SUPABASE_URL and SUPABASE_ANON_KEY (or SUPABASE_SERVICE_ROLE_KEY) are set."
        )
    return create_client(url, key)

def _get_bucket_name() -> str:
    bucket = os.getenv("STORAGE_BUCKET", "documents")
    if not bucket:
        bucket = "documents"
    return bucket

# PUBLIC_INTERFACE
@router.get(
    "",
    summary="List files",
    description="List the current user's files, optionally filtered by session_id.",
)
def list_files(session_id: Optional[str] = None, user=Depends(get_current_user)):
    """
    List files that belong to the current user.

    Query params:
    - session_id (optional): If provided, only list files linked to this session.

    Returns:
    - { "items": [ ...file rows... ] }
    """
    sb = _get_supabase()
    try:
        query = sb.table("files").select("*").eq("user_id", user["id"]).order("created_at", desc=True)
        if session_id:
            query = query.eq("session_id", session_id)
        resp = query.execute()
        rows = getattr(resp, "data", []) or []
        return {"items": rows}
    except Exception as e:
        # Hide internal details but keep informative message
        raise HTTPException(status_code=500, detail=f"Failed to list files: {e}")

# PUBLIC_INTERFACE
@router.post(
    "",
    summary="Upload file",
    description="Protected endpoint for uploading a file to Supabase Storage. Validates extension, MIME type, and size (<100MB). Inserts a metadata record in public.files.",
)
async def upload_file(
    file: UploadFile = File(..., description="The file to upload (.pdf, .docx, .txt, .xlsx)"),
    user=Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Upload a file to Supabase Storage and insert a metadata record.

    Request: multipart/form-data with a single 'file' field.
    Validations:
      - Extension must be one of: .pdf, .docx, .txt, .xlsx
      - MIME type must correspond to a supported type.
      - Size must be under 100MB (if provided by client; otherwise we still stream but reject on read if exceeds).

    Storage:
      - Uses Supabase Storage bucket from env STORAGE_BUCKET (default 'documents').
      - Path format: {user_id}/{yyyy}/{mm}/{dd}/{uuid4}_{safe_filename}

    Database:
      - Inserts into public.files with user_id, name, storage_path, mime_type, size.

    Returns:
      - JSON metadata with id (UUID), user_id, name, storage_path, mime_type, size, created_at (if available)
    """
    # Attempt to get size via headers if present (some servers/frameworks pass it)
    size_hint: Optional[int] = None
    try:
        # Starlette/UploadFile doesn't expose size directly; rely on header if provided
        size_header = file.headers.get("content-length") if hasattr(file, "headers") else None
        if size_header and size_header.isdigit():
            size_hint = int(size_header)
    except Exception:
        size_hint = None

    # Validate basic attributes before reading
    _validate_file(file, size_hint)

    # Read the entire file content (kept simple; production might stream-chunk and multipart upload)
    try:
        content: bytes = await file.read()
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to read uploaded file data.")

    actual_size = len(content)
    if actual_size > _MAX_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File too large: {actual_size} bytes. Max is 104857600 bytes (100MB).",
        )

    # Prepare storage path
    user_id = user["id"]
    ts = time.gmtime()
    y, m, d = ts.tm_year, ts.tm_mon, ts.tm_mday
    filename = file.filename or "upload"
    # Basic filename sanitization
    safe_name = "".join(ch for ch in filename if ch.isalnum() or ch in (" ", ".", "_", "-")).strip().replace(" ", "_")
    unique = uuid.uuid4()
    storage_path = f"{user_id}/{y:04d}/{m:02d}/{d:02d}/{unique}_{safe_name}"

    # Upload to Supabase Storage
    sb = _get_supabase()
    bucket = _get_bucket_name()

    try:
        sb.storage.from_(bucket).upload(
            path=storage_path,
            file=content,
            file_options={"content-type": file.content_type or "application/octet-stream"},
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Storage upload failed: {e}")

    # Insert DB record
    try:
        insert_payload = {
            "user_id": user_id,
            "name": filename,
            "storage_path": storage_path,
            "mime_type": file.content_type,
            "size": actual_size,
        }
        db_resp = sb.table("files").insert(insert_payload).select("*").single().execute()
        row = getattr(db_resp, "data", None)
        if not row:
            # Attempt to cleanup the uploaded object to avoid orphaned storage (best-effort)
            try:
                sb.storage.from_(bucket).remove([storage_path])
            except Exception:
                pass
            raise HTTPException(status_code=500, detail="Failed to create file record.")
    except HTTPException:
        raise
    except Exception as e:
        # Attempt cleanup
        try:
            sb.storage.from_(bucket).remove([storage_path])
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Database insert failed: {e}")

    # Return the inserted record as-is (ensures id/created_at, etc.)
    return row

# PUBLIC_INTERFACE
@router.delete(
    "/{file_id}",
    summary="Delete file",
    description="Delete a file you own: removes DB record and Storage object if found.",
)
def delete_file(file_id: str, user=Depends(get_current_user)):
    """
    Delete a user-owned file.

    Steps:
    1) Fetch the file by id and ensure it belongs to the current user.
    2) Remove the DB record.
    3) Attempt to delete the storage object. If storage deletion fails, surface a 502.
    """
    if not file_id:
        raise HTTPException(status_code=400, detail="file_id is required")

    sb = _get_supabase()
    bucket = _get_bucket_name()

    # Fetch file ensuring ownership
    try:
        resp = sb.table("files").select("*").eq("id", file_id).eq("user_id", user["id"]).single().execute()
        row = getattr(resp, "data", None)
    except Exception:
        row = None

    if not row:
        # Either doesn't exist or not owned by the user -> 404 (avoid leaking existence)
        raise HTTPException(status_code=404, detail="File not found")

    storage_path = row.get("storage_path")
    # First delete DB record (RLS ensures only owner can delete)
    try:
        sb.table("files").delete().eq("id", file_id).eq("user_id", user["id"]).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete file record: {e}")

    # Then attempt to delete storage object (best-effort; if it fails, return 502 so clients can retry/alert)
    if storage_path:
        try:
            sb.storage.from_(bucket).remove([storage_path])
        except Exception as e:
            # The DB record is already gone; report storage issue
            raise HTTPException(status_code=502, detail=f"Failed to remove storage object: {e}")

    return {"success": True, "id": file_id}
