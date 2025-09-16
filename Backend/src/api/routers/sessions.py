from fastapi import APIRouter, Depends, HTTPException
from supabase import create_client
import os

from ..deps import get_current_user
from ..models import SessionCreate, SessionUpdate, Session, SessionsList

router = APIRouter(prefix="/sessions", tags=["Sessions"])

def get_supabase():
    """Create Supabase client for session routes using SUPABASE_URL and key(s)."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        raise RuntimeError(
            "Supabase configuration missing. Ensure SUPABASE_URL and SUPABASE_ANON_KEY (or SUPABASE_SERVICE_ROLE_KEY) are set."
        )
    return create_client(url, key)

# PUBLIC_INTERFACE
@router.get("", response_model=SessionsList, summary="List sessions", description="List all sessions for the current user.")
def list_sessions(user=Depends(get_current_user)):
    sb = get_supabase()
    resp = (
        sb.table("sessions")
        .select("*")
        .eq("user_id", user["id"])
        .order("created_at", desc=True)
        .execute()
    )
    rows = getattr(resp, "data", []) or []
    items = [Session(**r) for r in rows]
    return SessionsList(items=items)

# PUBLIC_INTERFACE
@router.post("", response_model=Session, status_code=201, summary="Create session", description="Create a new session for the current user.")
def create_session(payload: SessionCreate, user=Depends(get_current_user)):
    sb = get_supabase()
    to_insert = {"user_id": user["id"], "title": payload.title}
    resp = sb.table("sessions").insert(to_insert).select("*").single().execute()
    row = getattr(resp, "data", None)
    if not row:
        raise HTTPException(status_code=400, detail="Failed to create session")
    return Session(**row)

# PUBLIC_INTERFACE
@router.get("/{session_id}", response_model=Session, summary="Get session", description="Fetch a session by ID (must belong to current user).")
def get_session(session_id: str, user=Depends(get_current_user)):
    sb = get_supabase()
    resp = sb.table("sessions").select("*").eq("id", session_id).eq("user_id", user["id"]).single().execute()
    row = getattr(resp, "data", None)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    return Session(**row)

# PUBLIC_INTERFACE
@router.get(
    "/{session_id}/files",
    summary="List files for a session",
    description="List files attached to a specific session. The session must exist and be owned by the current authenticated user.",
)
def list_session_files(session_id: str, user=Depends(get_current_user)):
    """
    List files for the given session owned by the current user.

    Behavior:
    - Validates that the session exists.
    - If the session exists but is not owned by the current user, returns 403.
    - If the session does not exist, returns 404.
    - On success, returns {"items": [...]} with file metadata rows.
    """
    sb = get_supabase()

    # First, verify session ownership or existence
    try:
        resp_owned = (
            sb.table("sessions")
            .select("id,user_id")
            .eq("id", session_id)
            .eq("user_id", user["id"])
            .single()
            .execute()
        )
        owned_row = getattr(resp_owned, "data", None)
    except Exception:
        owned_row = None

    if not owned_row:
        # Check if the session exists at all (service key preferred; anon may be restricted by RLS)
        try:
            resp_any = sb.table("sessions").select("id,user_id").eq("id", session_id).single().execute()
            any_row = getattr(resp_any, "data", None)
        except Exception:
            any_row = None

        if any_row is not None and any_row.get("user_id") != user["id"]:
            raise HTTPException(status_code=403, detail="Forbidden: session is not owned by user")
        raise HTTPException(status_code=404, detail="Session not found")

    # Fetch files linked to this session and owned by the user
    try:
        files_resp = (
            sb.table("files")
            .select("*")
            .eq("user_id", user["id"])
            .eq("session_id", session_id)
            .order("created_at", desc=True)
            .execute()
        )
        rows = getattr(files_resp, "data", []) or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list session files: {e}")

    # Normalize response fields
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
@router.patch("/{session_id}", response_model=Session, summary="Update session", description="Update a session's title (must belong to current user).")
def update_session(session_id: str, payload: SessionUpdate, user=Depends(get_current_user)):
    sb = get_supabase()
    updates = {}
    if payload.title is not None:
        updates["title"] = payload.title
    if not updates:
        # No-op; return existing if owned
        return get_session(session_id, user)

    resp = (
        sb.table("sessions")
        .update(updates)
        .eq("id", session_id)
        .eq("user_id", user["id"])
        .select("*")
        .single()
        .execute()
    )
    row = getattr(resp, "data", None)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found or not updated")
    return Session(**row)

# PUBLIC_INTERFACE
@router.delete("/{session_id}", status_code=204, summary="Delete session", description="Delete a session (must belong to current user).")
def delete_session(session_id: str, user=Depends(get_current_user)):
    sb = get_supabase()
    # Delete will respect RLS
    sb.table("sessions").delete().eq("id", session_id).eq("user_id", user["id"]).execute()
    # Return no content
    return
