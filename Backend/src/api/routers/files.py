import os
import re
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from starlette.responses import JSONResponse
from supabase import create_client, Client

from ..deps import get_current_user

router = APIRouter(prefix="/files", tags=["Files"])

# Internal helpers
_ALLOWED_EXTS = {".pdf", ".docx", ".txt", ".xlsx"}
_MIME_WHITELIST = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # docx
    "text/plain",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # xlsx
}
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _get_supabase() -> Client:
    """Create a Supabase client using env configuration."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        raise RuntimeError(
            "Supabase configuration missing. Ensure SUPABASE_URL and SUPABASE_ANON_KEY "
            "(or SUPABASE_SERVICE_ROLE_KEY) are set."
        )
    return create_client(url, key)


def _max_size_bytes() -> int:
    """Return max file size in bytes determined by env FILES_MAX_SIZE_MB (default 20MB)."""
    try:
        mb = int(os.getenv("FILES_MAX_SIZE_MB", "20"))
        if mb <= 0:
            mb = 20
    except Exception:
        mb = 20
    return mb * 1024 * 1024


def _get_storage_bucket() -> str:
    bucket = os.getenv("STORAGE_BUCKET", "").strip()
    if not bucket:
        # default bucket name if not provided; recommend configuring via env
        bucket = "uploads"
    return bucket


def _secure_filename(filename: str) -> str:
    """Sanitize filename to prevent path traversal and unsafe chars."""
    # Extract only the base name to avoid directories
    base = os.path.basename(filename)
    # Replace spaces and strip unsafe characters
    base = base.replace(" ", "_")
    base = _SAFE_NAME_RE.sub("_", base)
    # Ensure not empty
    return base or "file"


def _ext_of(name: str) -> str:
    name_lower = name.lower()
    for ext in _ALLOWED_EXTS:
        if name_lower.endswith(ext):
            return ext
    return os.path.splitext(name_lower)[1]


def _validate_extension_and_mime(filename: str, content_type: Optional[str]):
    ext = _ext_of(filename)
    if ext not in _ALLOWED_EXTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file extension. Allowed: {', '.join(sorted(_ALLOWED_EXTS))}",
        )
    # Accept if client mime is in whitelist. If client provided nothing, we accept based on ext.
    if content_type and content_type not in _MIME_WHITELIST:
        # Some clients may send generic 'application/octet-stream'; allow that if extension is valid.
        if content_type != "application/octet-stream":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unsupported MIME type.",
            )


def _validate_session_ownership(sb: Client, session_id: str, user_id: str):
    # Query sessions table with RLS protection (and service/anon key). Double-check ownership via filter.
    resp = (
        sb.table("sessions")
        .select("id,user_id")
        .eq("id", session_id)
        .eq("user_id", user_id)
        .single()
        .execute()
    )
    data = getattr(resp, "data", None)
    if not data:
        # Determine if session exists but is not owned, or does not exist.
        # Try to see if session exists at all (without user filter) using service role if available.
        try:
            resp_any = sb.table("sessions").select("id").eq("id", session_id).single().execute()
            exists_any = getattr(resp_any, "data", None) is not None
        except Exception:
            exists_any = False
        if exists_any:
            # Exists but not owned
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden: session is not owned by user")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")


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
    """
    List files for the current user.

    - If session_id is provided, validates that the session exists and is owned by the current user.
    - Applies Row Level Security (RLS) via Supabase; only files with user_id == auth.uid() are returned.
    - Returns a list of file metadata with fields:
        id, name, storage_path, mime_type, size, session_id, created_at

    Errors:
    - 400: invalid input
    - 401: not authenticated
    - 403/404: session forbidden/not found (when session_id is provided)
    - 500: backend failure
    """
    if not user or "id" not in user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    sb = _get_supabase()

    # If filtering by session, ensure the session is owned by the user
    if session_id:
        _validate_session_ownership(sb, session_id, user["id"])

    try:
        query = sb.table("files").select("*").eq("user_id", user["id"]).order("created_at", desc=True)
        if session_id:
            query = query.eq("session_id", session_id)
        resp = query.execute()
        rows = getattr(resp, "data", []) or []
    except HTTPException:
        # Propagate known HTTP errors
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list files: {e}")

    # Shape response items
    items = []
    for r in rows:
        items.append(
            {
                "id": r.get("id"),
                "name": r.get("name"),
                "storage_path": r.get("storage_path"),
                "type": r.get("mime_type"),
                "size": r.get("size"),
                "session_id": r.get("session_id"),
                "created_at": r.get("created_at"),
            }
        )

    return {"items": items}

# PUBLIC_INTERFACE
@router.post(
    "",
    summary="Upload file",
    description=(
        "Authenticated file upload endpoint accepting multipart/form-data for .pdf, .docx, .txt, .xlsx. "
        "Validates size and type, stores in Supabase Storage, and persists metadata in DB. "
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
    """
    Upload a file to Supabase Storage and persist metadata to the database.

    - Accepts multipart/form-data with fields:
      - file: UploadFile (.pdf, .docx, .txt, .xlsx only)
      - session_id: optional session to link to; validated for ownership
    - Enforces max file size via env FILES_MAX_SIZE_MB (default 20MB)
    - Validates extension and MIME, secures filename, and prevents path traversal
    - Stores binary in Supabase Storage at {user_id}/{YYYY-MM-DD}/{uuid}-{filename}
    - Inserts metadata row to public.files with RLS applied
    - Returns created metadata record

    Errors:
    - 400: invalid file/type
    - 401: not authenticated
    - 403/404: session ownership/not found
    - 413: file too large
    - 500: storage/DB failures
    """
    if not user or "id" not in user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    user_id = user["id"]
    if not file or not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No file provided")

    safe_name = _secure_filename(file.filename)
    _validate_extension_and_mime(safe_name, file.content_type)

    # Check session ownership if provided
    sb = _get_supabase()
    if session_id:
        _validate_session_ownership(sb, session_id, user_id)

    # Enforce size by reading the stream in chunks and counting bytes
    max_bytes = _max_size_bytes()
    data_chunks = []
    total = 0
    try:
        while True:
            chunk = await file.read(1024 * 1024)  # 1MB chunks
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"File too large. Max {max_bytes // (1024 * 1024)} MB",
                )
            data_chunks.append(chunk)
    finally:
        await file.close()

    binary = b"".join(data_chunks)

    # Build storage path
    today = datetime.utcnow().strftime("%Y-%m-%d")
    unique = str(uuid.uuid4())
    storage_key = f"{user_id}/{today}/{unique}-{safe_name}"

    bucket = _get_storage_bucket()

    # Upload to Supabase Storage
    try:
        # Create bucket if it does not exist (best-effort; ignore errors if already exists)
        try:
            sb.storage.create_bucket(bucket)
        except Exception:
            pass

        # Upload the file
        # Set content_type if provided; otherwise let storage infer or set generic
        content_type = file.content_type or "application/octet-stream"
        sb.storage.from_(bucket).upload(path=storage_key, file=binary, file_options={"contentType": content_type})
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Storage upload failed: {e}")

    # Insert metadata into DB
    try:
        insert_payload = {
            "user_id": user_id,
            "session_id": session_id,
            "name": safe_name,
            "storage_path": f"{bucket}/{storage_key}",
            "mime_type": file.content_type,
            "size": total,
        }
        resp = sb.table("files").insert(insert_payload).select("*").single().execute()
        row = getattr(resp, "data", None)
        if not row:
            # rollback storage? For now, return 500 with guidance
            raise HTTPException(status_code=500, detail="Failed to persist file metadata")
    except HTTPException:
        # Bubble up
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to persist file metadata: {e}")

    # Standard JSON response with metadata
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "id": row["id"],
            "name": row["name"],
            "type": row.get("mime_type"),
            "storage_path": row["storage_path"],
            "session_id": row.get("session_id"),
            "size": row.get("size"),
            "created_at": row.get("created_at"),
        },
    )

# PUBLIC_INTERFACE
@router.delete("/{file_id}", summary="Delete file (stub)", description="Protected stub endpoint for deleting a file.")
def delete_file(file_id: str, user=Depends(get_current_user)):
    return {"success": True, "note": f"Delete stub for {file_id}. To be implemented."}
