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
    """List all sessions for the authenticated user, ordered by most recent."""
    sb = get_supabase()
    try:
        resp = (
            sb.table("sessions")
            .select("*")
            .eq("user_id", user["id"])
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list sessions: {e}")

    rows = getattr(resp, "data", []) or []
    items = [Session(**r) for r in rows]
    return SessionsList(items=items)

# PUBLIC_INTERFACE
@router.post("", response_model=Session, status_code=201, summary="Create session", description="Create a new session for the current user.")
def create_session(payload: SessionCreate, user=Depends(get_current_user)):
    """Create a new session record owned by the current user.

    Note:
    - supabase-py (2.x) does not support chaining `.select()` or `.single()` directly after `.insert()`.
      Instead, call `.insert(...).execute()` and read the returned `.data`.
    - `.data` may be a list (multiple rows) or a dict (single row) depending on SDK version/behavior.
    """
    sb = get_supabase()
    to_insert = {"user_id": user["id"], "title": payload.title}

    try:
        insert_resp = sb.table("sessions").insert(to_insert).execute()
    except Exception as e:
        # Most common causes: RLS violations or network errors
        raise HTTPException(status_code=400, detail=f"Failed to create session: {e}")

    data = getattr(insert_resp, "data", None)

    # Normalize possible return types from supabase-py
    created_row = None
    if isinstance(data, list) and data:
        created_row = data[0]
    elif isinstance(data, dict) and data:
        created_row = data
    else:
        # As a fallback (rare), attempt to fetch the most recent session for this user
        try:
            sel = (
                sb.table("sessions")
                .select("*")
                .eq("user_id", user["id"])
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            rows = getattr(sel, "data", []) or []
            if rows:
                created_row = rows[0]
        except Exception:
            created_row = None

    if not created_row:
        raise HTTPException(status_code=400, detail="Failed to create session")

    return Session(**created_row)

# PUBLIC_INTERFACE
@router.get("/{session_id}", response_model=Session, summary="Get session", description="Fetch a session by ID (must belong to current user).")
def get_session(session_id: str, user=Depends(get_current_user)):
    """Fetch a single session by ID, ensuring it belongs to the current user."""
    sb = get_supabase()
    try:
        resp = (
            sb.table("sessions")
            .select("*")
            .eq("id", session_id)
            .eq("user_id", user["id"])
            .single()
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Session not found: {e}")

    row = getattr(resp, "data", None)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    return Session(**row)

# PUBLIC_INTERFACE
@router.patch("/{session_id}", response_model=Session, summary="Update session", description="Update a session's title (must belong to current user).")
def update_session(session_id: str, payload: SessionUpdate, user=Depends(get_current_user)):
    """Update the session title if provided. Returns the updated session."""
    sb = get_supabase()
    updates = {}
    if payload.title is not None:
        updates["title"] = payload.title
    if not updates:
        # No-op; return existing if owned
        return get_session(session_id, user)

    try:
        # supabase-py may not support .select().single() after update in some versions.
        # Strategy: perform update(), then select the row.
        upd_resp = (
            sb.table("sessions")
            .update(updates)
            .eq("id", session_id)
            .eq("user_id", user["id"])
            .execute()
        )
        updated_data = getattr(upd_resp, "data", None)

        if isinstance(updated_data, list) and updated_data:
            row = updated_data[0]
        elif isinstance(updated_data, dict) and updated_data:
            row = updated_data
        else:
            # Fallback: select the row explicitly
            sel = (
                sb.table("sessions")
                .select("*")
                .eq("id", session_id)
                .eq("user_id", user["id"])
                .single()
                .execute()
            )
            row = getattr(sel, "data", None)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to update session: {e}")

    if not row:
        raise HTTPException(status_code=404, detail="Session not found or not updated")
    return Session(**row)

# PUBLIC_INTERFACE
@router.delete("/{session_id}", status_code=204, summary="Delete session", description="Delete a session (must belong to current user).")
def delete_session(session_id: str, user=Depends(get_current_user)):
    """Delete a session that belongs to the current user. No content is returned."""
    sb = get_supabase()
    try:
        # Delete will respect RLS
        sb.table("sessions").delete().eq("id", session_id).eq("user_id", user["id"]).execute()
    except Exception:
        # If already deleted or not found, respond with 204 for idempotency or 404; choose 204 to keep it simple.
        # Could log the exception if logging is set up.
        pass
    # Return no content
    return
