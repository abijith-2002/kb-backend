import os
import io
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from supabase import create_client, Client

from ..deps import get_current_user
from ..models import FileMeta, FilesList

router = APIRouter(prefix="/files", tags=["Files"])

ALLOWED_EXTS = {".docx", ".pdf", ".txt", ".xlsx"}
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB


def get_supabase() -> Client:
    """Create Supabase client for file routes using SUPABASE_URL and key(s)."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        raise RuntimeError(
            "Supabase configuration missing. Ensure SUPABASE_URL and SUPABASE_ANON_KEY (or SUPABASE_SERVICE_ROLE_KEY) are set."
        )
    return create_client(url, key)


def _validate_filename_and_size(upload: UploadFile, size: int):
    name = upload.filename or ""
    lower = name.lower()
    ext = ""
    if "." in lower:
        ext = lower[lower.rfind(".") :]
    if ext not in ALLOWED_EXTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(ALLOWED_EXTS))}",
        )
    if size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File too large. Maximum size is 100 MB.",
        )


# PUBLIC_INTERFACE
@router.get(
    "",
    response_model=FilesList,
    summary="List files by current user",
    description="Protected endpoint to list files for the current user. Optionally filter by session_id via query parameter.",
)
def list_files(session_id: Optional[str] = None, user=Depends(get_current_user)):
    """
    List files owned by the current user. Optionally filter by session_id.
    """
    sb = get_supabase()
    q = sb.table("files").select("*").eq("user_id", user["id"]).order("created_at", desc=True)
    if session_id:
        q = q.eq("session_id", session_id)
    resp = q.execute()
    rows = getattr(resp, "data", []) or []
    items = [FileMeta(**r) for r in rows]
    return FilesList(items=items)


# PUBLIC_INTERFACE
@router.post(
    "",
    response_model=FileMeta,
    summary="Upload file",
    description="Upload a file for a given session. Accepts .docx, .pdf, .txt, .xlsx and size < 100MB. Stores in Supabase Storage and records metadata.",
)
async def upload_file(
    session_id: str = Form(..., description="Target session ID"),
    file: UploadFile = File(..., description="File to upload"),
    user=Depends(get_current_user),
):
    """
    Upload a file to Supabase Storage path:
      user/{user_id}/session/{session_id}/{filename}

    Steps:
    - Validate extension and size (< 100 MB)
    - Verify session ownership via RLS on select
    - Upload to storage bucket (configured externally, e.g., 'user-content')
    - Insert metadata row into public.files and return the row
    """
    sb = get_supabase()

    # Verify the session belongs to the user (RLS will enforce, but we explicitly check)
    sresp = (
        sb.table("sessions").select("id").eq("id", session_id).eq("user_id", user["id"]).single().execute()
    )
    if not getattr(sresp, "data", None):
        raise HTTPException(status_code=404, detail="Session not found")

    # Read file content to buffer to compute size and upload
    data = await file.read()
    size = len(data)
    _validate_filename_and_size(file, size)

    filename = file.filename or "upload.bin"
    # Storage path within the bucket
    storage_path = f"user/{user['id']}/session/{session_id}/{filename}"
    bucket = os.getenv("SUPABASE_STORAGE_BUCKET", "user-content")

    # Upload to Supabase storage
    try:
        sb.storage.from_(bucket).upload(storage_path, io.BytesIO(data), file_options={"contentType": file.content_type or "application/octet-stream", "upsert": True})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload to storage: {e}")

    # Insert metadata row
    try:
        meta = {
            "user_id": user["id"],
            "session_id": session_id,
            "name": filename,
            "storage_path": f"{bucket}/{storage_path}",
            "mime_type": file.content_type,
            "size": size,
        }
        iresp = sb.table("files").insert(meta).select("*").single().execute()
        row = getattr(iresp, "data", None)
        if not row:
            raise HTTPException(status_code=500, detail="Failed to persist file metadata")
        return FileMeta(**row)
    except HTTPException:
        # best-effort cleanup: try deleting storage object
        try:
            sb.storage.from_(bucket).remove([storage_path])
        except Exception:
            pass
        raise
    except Exception as e:
        # cleanup storage on failure
        try:
            sb.storage.from_(bucket).remove([storage_path])
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Failed to persist file metadata: {e}")


# PUBLIC_INTERFACE
@router.delete(
    "/{file_id}",
    summary="Delete file by ID",
    description="Delete file metadata and storage object. Must be owned by current user. If linked to a session, that session must be owned by the user.",
)
def delete_file(file_id: str, user=Depends(get_current_user)):
    """
    Delete a file that belongs to the current user:
    - Verify ownership by selecting the file row (RLS limits access)
    - Remove metadata row
    - Remove object from storage
    - Stub: vector store cleanup is a no-op in this increment
    """
    sb = get_supabase()

    # Fetch the file row under RLS to confirm ownership
    fresp = sb.table("files").select("*").eq("id", file_id).eq("user_id", user["id"]).single().execute()
    file_row = getattr(fresp, "data", None)
    if not file_row:
        raise HTTPException(status_code=404, detail="File not found")

    bucket_and_path = file_row.get("storage_path", "")
    if "/" in bucket_and_path:
        bucket = bucket_and_path.split("/", 1)[0]
        path = bucket_and_path.split("/", 1)[1]
    else:
        bucket = os.getenv("SUPABASE_STORAGE_BUCKET", "user-content")
        path = bucket_and_path

    # Delete DB row first (RLS enforced)
    sb.table("files").delete().eq("id", file_id).eq("user_id", user["id"]).execute()

    # Then remove from storage (best-effort)
    try:
        sb.storage.from_(bucket).remove([path])
    except Exception:
        # ignore storage errors to keep idempotency
        pass

    return {"success": True}
