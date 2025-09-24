from fastapi import APIRouter, Depends, HTTPException, status
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
@router.get(
    "",
    response_model=SessionsList,
    summary="List sessions",
    description="List all sessions for the current user. Requires Authorization: Bearer <token>."
)
def list_sessions(user=Depends(get_current_user)):
    """List sessions owned by the current user.

    Returns:
      SessionsList: Items ordered by created_at desc.
    """
    try:
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
    except HTTPException:
        # bubble up auth errors, etc.
        raise
    except Exception as e:
        # Convert unexpected errors to 500 with a clear message
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to list sessions: {e}")

# PUBLIC_INTERFACE
@router.post(
    "",
    response_model=Session,
    status_code=201,
    summary="Create session",
    description="Create a new session for the current user."
)
def create_session(payload: SessionCreate, user=Depends(get_current_user)):
    """Create a session row for the current user."""
    try:
        sb = get_supabase()
        to_insert = {"user_id": user["id"], "title": payload.title}
        resp = sb.table("sessions").insert(to_insert).select("*").single().execute()
        row = getattr(resp, "data", None)
        if not row:
            raise HTTPException(status_code=400, detail="Failed to create session")
        return Session(**row)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create session: {e}")

# PUBLIC_INTERFACE
@router.get(
    "/{session_id}",
    response_model=Session,
    summary="Get session",
    description="Fetch a session by ID (must belong to current user)."
)
def get_session(session_id: str, user=Depends(get_current_user)):
    """Get a single session by ID if it belongs to the current user."""
    try:
        sb = get_supabase()
        resp = (
            sb.table("sessions")
            .select("*")
            .eq("id", session_id)
            .eq("user_id", user["id"])
            .single()
            .execute()
        )
        row = getattr(resp, "data", None)
        if not row:
            raise HTTPException(status_code=404, detail="Session not found")
        return Session(**row)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get session: {e}")

# PUBLIC_INTERFACE
@router.patch(
    "/{session_id}",
    response_model=Session,
    summary="Update session",
    description="Update a session's title (must belong to current user)."
)
def update_session(session_id: str, payload: SessionUpdate, user=Depends(get_current_user)):
    """Update a session title for the current user; returns updated row."""
    try:
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
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update session: {e}")

# PUBLIC_INTERFACE
@router.delete(
    "/{session_id}",
    status_code=204,
    summary="Delete session",
    description="Delete a session (must belong to current user)."
)
def delete_session(session_id: str, user=Depends(get_current_user)):
    """Delete a session if owned by the current user."""
    try:
        sb = get_supabase()
        # Delete will respect RLS; if not found or not owned, delete affects 0 rows.
        sb.table("sessions").delete().eq("id", session_id).eq("user_id", user["id"]).execute()
        # Return no content
        return
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete session: {e}")
