import os
import uuid
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from supabase import create_client, Client

from ..deps import get_current_user
from ..models_files import FilesList, FileItem, FileUploadResponse

router = APIRouter(prefix="/files", tags=["Files"])

def get_supabase() -> Client:
    """Create Supabase client for file routes using SUPABASE_URL and key(s)."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        raise RuntimeError(
            "Supabase configuration missing. Ensure SUPABASE_URL and SUPABASE_ANON_KEY (or SUPABASE_SERVICE_ROLE_KEY) are set."
        )
    return create_client(url, key)

def get_bucket_name() -> str:
    """Resolve the Supabase Storage bucket name from env or default."""
    return os.getenv("SUPABASE_STORAGE_BUCKET", "documents")

SUPPORTED_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "text/plain",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # .xlsx
}

SUPPORTED_EXTS = {".pdf", ".docx", ".txt", ".xlsx"}

def _ext_from_filename(name: str) -> str:
    dot = name.rfind(".")
    return name[dot:].lower() if dot != -1 else ""

def _validate_upload(file: UploadFile) -> None:
    ext = _ext_from_filename(file.filename or "")
    mime = (file.content_type or "").lower()
    if ext not in SUPPORTED_EXTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file extension '{ext}'. Allowed: {', '.join(sorted(SUPPORTED_EXTS))}",
        )
    # For safety, allow upload even if content_type missing, but if present and not in allowed, block.
    if mime and mime not in SUPPORTED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported MIME type '{mime}'. Allowed: {', '.join(sorted(SUPPORTED_MIME_TYPES))}",
        )

def _compose_storage_path(user_id: str, file_id: str, filename: str) -> str:
    # Store under user-id namespace to avoid collisions and to simplify auditing.
    safe_name = filename.replace("\\", "/").split("/")[-1]
    return f"{user_id}/{file_id}/{safe_name}"

# PUBLIC_INTERFACE
@router.get(
    "",
    response_model=FilesList,
    summary="List files",
    description="List all stored files for the current authenticated user.",
)
def list_files(user=Depends(get_current_user)):
    """
    Return the list of file metadata records for the current user.

    This only reads from the files metadata table. It does not touch storage.
    """
    sb = get_supabase()
    resp = (
        sb.table("files")
        .select("*")
        .eq("user_id", user["id"])
        .order("created_at", desc=True)
        .execute()
    )
    rows: List[dict] = getattr(resp, "data", []) or []
    items = [FileItem(**r) for r in rows]
    return FilesList(items=items)

# PUBLIC_INTERFACE
@router.post(
    "",
    response_model=FileUploadResponse,
    status_code=201,
    summary="Upload file",
    description="Upload a file via multipart/form-data. Stores the file in Supabase Storage and records metadata in the files table.",
)
async def upload_file(
    uploaded_file: UploadFile = File(..., description="The file to upload (PDF, DOCX, TXT, XLSX)."),
    session_id: Optional[str] = Form(None, description="Optional session to associate this file with."),
    user=Depends(get_current_user),
):
    """
    Accepts multipart file upload, validates allowed types, uploads to Supabase Storage,
    and inserts a metadata row into public.files.
    """
    _validate_upload(uploaded_file)

    sb = get_supabase()
    bucket = get_bucket_name()

    file_id = str(uuid.uuid4())
    storage_path = _compose_storage_path(user["id"], file_id, uploaded_file.filename or "file")

    # Read file bytes (no processing)
    contents = await uploaded_file.read()
    size = len(contents)
    mime = (uploaded_file.content_type or "").lower() or None

    # Ensure bucket exists (best-effort). If bucket already exists, ignore errors.
    try:
        # list_buckets to check, create if missing
        buckets = sb.storage.list_buckets()
        names = {b.name for b in (buckets or [])}
        if bucket not in names:
            try:
                sb.storage.create_bucket(bucket, public=False)
            except Exception:
                # bucket may already exist or permission denied; proceed
                pass
    except Exception:
        # storage.list_buckets might require elevated perms; proceed assuming bucket exists.
        pass

    # Upload to storage
    try:
        sb.storage.from_(bucket).upload(path=storage_path, file=contents, file_options={"contentType": mime or "application/octet-stream", "upsert": False})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload to storage: {e}")

    # Insert metadata
    record = {
        "id": file_id,
        "user_id": user["id"],
        "session_id": session_id,
        "name": uploaded_file.filename,
        "storage_path": storage_path,
        "mime_type": mime,
        "size": size,
    }
    try:
        meta = sb.table("files").insert(record).select("*").single().execute()
    except Exception as e:
        # Roll back storage upload best-effort if metadata insert fails
        try:
            sb.storage.from_(bucket).remove([storage_path])
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Failed to insert metadata: {e}")

    row = getattr(meta, "data", None)
    if not row:
        # Same rollback best-effort
        try:
            sb.storage.from_(bucket).remove([storage_path])
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="Failed to persist file metadata")

    return FileUploadResponse(
        id=row["id"],
        name=row["name"],
        storage_path=row["storage_path"],
        mime_type=row.get("mime_type"),
        size=row.get("size"),
        session_id=row.get("session_id"),
    )

# PUBLIC_INTERFACE
@router.delete(
    "/{file_id}",
    status_code=204,
    summary="Delete file",
    description="Delete a user's file: removes storage object and metadata record if owned by current user.",
)
def delete_file(file_id: str, user=Depends(get_current_user)):
    """
    Deletes a file the current user owns:
    1. Fetch metadata (ensures ownership via RLS).
    2. Delete object from Supabase Storage.
    3. Delete metadata row.
    """
    sb = get_supabase()
    bucket = get_bucket_name()

    # Fetch metadata (RLS ensures we only see owned file)
    resp = (
        sb.table("files")
        .select("*")
        .eq("id", file_id)
        .eq("user_id", user["id"])
        .single()
        .execute()
    )
    meta = getattr(resp, "data", None)
    if not meta:
        # Not found or not owned
        raise HTTPException(status_code=404, detail="File not found")

    storage_path = meta["storage_path"]

    # Attempt to delete from storage (best-effort; continue to metadata deletion even if storage already missing)
    try:
        sb.storage.from_(bucket).remove([storage_path])
    except Exception:
        # ignore to ensure metadata is removed to prevent orphans
        pass

    # Delete metadata
    sb.table("files").delete().eq("id", file_id).eq("user_id", user["id"]).execute()

    return
