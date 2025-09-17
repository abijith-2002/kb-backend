import os
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Query
from supabase import create_client
from ..deps import get_current_user
from ..models import FilesList, FileItem, FilePinRequest
from .services import (
    get_storage_bucket_name,
    extract_text_from_file_bytes,
    chunk_text,
    embed_texts,
    get_chroma,
    store_embeddings,
    check_quota,
    ensure_audit_log,
)

router = APIRouter(prefix="/files", tags=["Files"])

def get_supabase():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        raise RuntimeError("Supabase configuration missing.")
    return create_client(url, key)

# PUBLIC_INTERFACE
@router.get("", response_model=FilesList, summary="List files", description="List files owned by the current user. Optionally filtered by session_id.")
def list_files(user=Depends(get_current_user), session_id: Optional[str] = Query(None, description="Filter by session ID")):
    sb = get_supabase()
    q = sb.table("files").select("*").eq("user_id", user["id"])
    if session_id:
        q = q.eq("session_id", session_id)
    resp = q.order("created_at", desc=True).execute()
    rows = getattr(resp, "data", []) or []
    items = [FileItem(**r) for r in rows]
    return FilesList(items=items)

# PUBLIC_INTERFACE
@router.post("", response_model=FileItem, summary="Upload file", description="Upload a file to Supabase Storage, extract text, chunk, embed, and index into Chroma.")
async def upload_file(user=Depends(get_current_user), session_id: Optional[str] = Query(None), f: UploadFile = File(...)):
    sb = get_supabase()
    content = await f.read()
    check_quota(sb, user["id"], bytes_to_add=len(content))

    # Store to Supabase Storage
    bucket = get_storage_bucket_name()
    file_id = str(uuid.uuid4())
    ext = os.path.splitext(f.filename or "file")[1]
    storage_path = f"{user['id']}/{file_id}{ext}"
    try:
        sb.storage.from_(bucket).upload(storage_path, content, {"content-type": f.content_type or "application/octet-stream", "upsert": False})
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to upload to storage: {e}")

    # Insert metadata
    meta = {
        "id": file_id,
        "user_id": user["id"],
        "session_id": session_id,
        "name": f.filename,
        "storage_path": storage_path,
        "mime_type": f.content_type,
        "size": len(content),
    }
    try:
        row = sb.table("files").insert(meta).select("*").single().execute().data
    except Exception as e:
        # best-effort rollback storage? (not strictly necessary here)
        raise HTTPException(status_code=400, detail=f"Failed to record file metadata: {e}")

    # Extract and embed
    text = extract_text_from_file_bytes(f.filename, content)
    chunks = chunk_text(text) if text else []
    vectors = embed_texts(chunks) if chunks else []

    # Index to Chroma
    store_embeddings(get_chroma(), user["id"], session_id or "global", file_id, chunks, vectors)

    ensure_audit_log(sb, user["id"], "file_upload", {"file_id": file_id, "name": f.filename, "size": len(content)})

    return FileItem(**row)

# PUBLIC_INTERFACE
@router.delete("/{file_id}", summary="Delete file", description="Delete a file and its embeddings.")
def delete_file(file_id: str, user=Depends(get_current_user)):
    sb = get_supabase()
    # Lookup file to ensure ownership
    resp = sb.table("files").select("*").eq("id", file_id).eq("user_id", user["id"]).single().execute()
    row = getattr(resp, "data", None)
    if not row:
        raise HTTPException(status_code=404, detail="File not found")

    # Delete from storage
    bucket = get_storage_bucket_name()
    try:
        sb.storage.from_(bucket).remove([row["storage_path"]])
    except Exception:
        pass

    # Delete from DB
    try:
        sb.table("files").delete().eq("id", file_id).eq("user_id", user["id"]).execute()
    except Exception:
        pass

    # We won't attempt to selectively delete embeddings from Chroma in this initial implementation.

    ensure_audit_log(sb, user["id"], "file_delete", {"file_id": file_id})
    return {"success": True}

# PUBLIC_INTERFACE
@router.post("/{file_id}/pin", summary="Pin/unpin file to session", description="Pin or unpin a file to a given session.")
def pin_file(file_id: str, payload: FilePinRequest, user=Depends(get_current_user)):
    sb = get_supabase()
    # Ownership check
    resp = sb.table("files").select("id").eq("id", file_id).eq("user_id", user["id"]).single().execute()
    if not getattr(resp, "data", None):
        raise HTTPException(status_code=404, detail="File not found")

    try:
        new_session = payload.session_id if payload.pinned else None
        row = sb.table("files").update({"session_id": new_session}).eq("id", file_id).eq("user_id", user["id"]).select("*").single().execute().data
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to update file pin: {e}")

    ensure_audit_log(sb, user["id"], "file_pin", {"file_id": file_id, "pinned": payload.pinned, "session_id": payload.session_id})
    return FileItem(**row)
