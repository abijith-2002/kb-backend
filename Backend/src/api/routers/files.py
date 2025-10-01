import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query, Request
from fastapi import status
from supabase import create_client, Client

from ..deps import get_current_user, get_supabase_user_scoped
from ..models import FileItem, FilesList

router = APIRouter(prefix="/files", tags=["Files"])

ALLOWED_EXTS = {".docx", ".pdf", ".txt", ".xlsx"}
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB
STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "user-files")

def get_supabase() -> Client:
    """Create a Supabase client using env configuration."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        raise RuntimeError(
            "Supabase configuration missing. Ensure SUPABASE_URL and SUPABASE_ANON_KEY (or SUPABASE_SERVICE_ROLE_KEY) are set."
        )
    return create_client(url, key)

def _ext(name: str) -> str:
    name_lower = (name or "").lower()
    dot = name_lower.rfind(".")
    return name_lower[dot:] if dot != -1 else ""

# PUBLIC_INTERFACE
@router.get(
    "",
    response_model=FilesList,
    summary="List files",
    description="List files for the current user. Optionally filter by session_id.",
)
def list_files(
    session_id: Optional[str] = Query(None, description="Filter files by session ID"),
    user=Depends(get_current_user),
    request: Request = None,
):
    """
    List files owned by the current user. Optional filter by session_id.
    """
    # Use a client that carries the end-user JWT so RLS sees auth.uid()
    sb = get_supabase_user_scoped(request)

    q = sb.table("files").select("*").eq("user_id", user["id"]).order("created_at", desc=True)
    if session_id:
        q = q.eq("session_id", session_id)
    resp = q.execute()
    rows = getattr(resp, "data", []) or []
    items = [FileItem(**r) for r in rows]
    return FilesList(items=items)

# PUBLIC_INTERFACE
@router.post(
    "",
    response_model=FileItem,
    status_code=status.HTTP_201_CREATED,
    summary="Upload file",
    description="Upload a file for a session. Accepts .docx, .pdf, .txt, .xlsx. Max 100 MB. Stores in Supabase Storage and inserts metadata.",
)
async def upload_file(
    session_id: str = Form(..., description="Target session ID"),
    upload: UploadFile = File(..., description="File to upload (.docx, .pdf, .txt, .xlsx)"),
    user=Depends(get_current_user),
    request: Request = None,
):
    """
    Upload a file associated to a session:
    - Validates extension and approximate size limit.
    - Stores object in Supabase Storage under user/{user_id}/session/{session_id}/{filename}.
    - Inserts metadata row into public.files and returns the record.
    """
    # Build client with user's JWT to satisfy RLS
    sb = get_supabase_user_scoped(request)

    # Verify session belongs to user (explicit check for clearer error)
    sresp = sb.table("sessions").select("id,user_id").eq("id", session_id).single().execute()
    srow = getattr(sresp, "data", None)
    if not srow or srow.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="Session not found")

    filename = upload.filename or "upload.bin"
    ext = _ext(filename)
    if ext not in ALLOWED_EXTS:
        raise HTTPException(status_code=400, detail="Unsupported file type. Allowed: .docx, .pdf, .txt, .xlsx")

    # Note: UploadFile doesn't expose size reliably. We can enforce by reading content in memory up to limit.
    data = await upload.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File too large. Max 100 MB")

    storage_path = f"user/{user['id']}/session/{session_id}/{filename}"

    # Upload to Supabase Storage
    try:
        sb.storage.from_(STORAGE_BUCKET).upload(
            storage_path,
            data,
            {"content-type": upload.content_type or "application/octet-stream", "x-upsert": "true"},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to store file: {e}")

    # Insert metadata
    meta = {
        "user_id": user["id"],
        "session_id": session_id,
        "name": filename,
        "storage_path": storage_path,
        "mime_type": upload.content_type,
        "size": len(data),
    }
    try:
        # supabase-py may not support chaining .select() after insert; rely on returned data
        iresp = sb.table("files").insert(meta).execute()
    except Exception as e:
        # Try to roll back storage object best-effort
        try:
            sb.storage.from_(STORAGE_BUCKET).remove([storage_path])
        except Exception:
            pass
        msg = str(e)
        if "row level security" in msg.lower() or "rls" in msg.lower() or "permission" in msg.lower():
            raise HTTPException(
                status_code=401,
                detail="Unauthorized by RLS while inserting files. Ensure backend uses user JWT for DB calls and Supabase policies allow insert with auth.uid() = user_id. Verify SUPABASE_SERVICE_ROLE_KEY is set or Authorization header is forwarded.",
            )
        raise HTTPException(status_code=500, detail=f"Failed to save metadata: {e}")

    # Normalize response shape: list (rows) or dict (single)
    row = None
    rows = getattr(iresp, "data", []) or []
    if isinstance(rows, list) and rows:
        row = rows[0]
    elif isinstance(rows, dict) and rows:
        row = rows

    if not row:
        # Fallback to an immediate select by unique-ish storage_path and owner
        sel = (
            sb.table("files")
            .select("*")
            .eq("user_id", user["id"])
            .eq("storage_path", storage_path)
            .limit(1)
            .execute()
        )
        rows2 = getattr(sel, "data", []) or []
        row = rows2[0] if rows2 else None

    if not row:
        raise HTTPException(status_code=500, detail="Upload succeeded but metadata response missing")
    return FileItem(**row)

# PUBLIC_INTERFACE
@router.delete(
    "/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete file",
    description="Delete a file by ID if owned by current user. Removes storage object and metadata.",
)
def delete_file(file_id: str, user=Depends(get_current_user), request: Request = None):
    """
    Delete a file object and its metadata if owned by the user.
    """
    # Build a Supabase client carrying the user's JWT for RLS-authorized DB calls
    sb = get_supabase_user_scoped(request)

    # Fetch file to verify ownership and get storage path
    fresp = sb.table("files").select("*").eq("id", file_id).eq("user_id", user["id"]).single().execute()
    row = getattr(fresp, "data", None)
    if not row:
        raise HTTPException(status_code=404, detail="File not found")

    storage_path = row.get("storage_path")
    # Remove storage object (best-effort)
    try:
        if storage_path:
            sb.storage.from_(STORAGE_BUCKET).remove([storage_path])
    except Exception:
        # Best effort, continue to delete metadata
        pass

    # Delete metadata
    sb.table("files").delete().eq("id", file_id).eq("user_id", user["id"]).execute()
    return

# PUBLIC_INTERFACE
@router.delete(
    "/sessions/{session_id}/files/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a file for a session",
    description="Delete a file ensuring it belongs to the given session and user.",
)
def delete_session_file(session_id: str, file_id: str, user=Depends(get_current_user), request: Request = None):
    """
    Delete a file constrained to a specific session to match frontend route plan.
    """
    sb = get_supabase_user_scoped(request)

    # Verify record matches session and owner
    f = sb.table("files").select("*").eq("id", file_id).eq("user_id", user["id"]).eq("session_id", session_id).single().execute()
    row = getattr(f, "data", None)
    if not row:
        raise HTTPException(status_code=404, detail="File not found for this session")

    storage_path = row.get("storage_path")
    try:
        if storage_path:
            sb.storage.from_(STORAGE_BUCKET).remove([storage_path])
    except Exception:
        pass

    sb.table("files").delete().eq("id", file_id).eq("user_id", user["id"]).eq("session_id", session_id).execute()
    # Future: cascade deletion in vector store (Chroma) – no-op for Increment 2
    return
