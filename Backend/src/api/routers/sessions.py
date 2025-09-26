from fastapi import APIRouter, Depends, HTTPException, status
from supabase import create_client
import os
from datetime import datetime, timezone
import uuid

from ..deps import get_current_user
from ..models import SessionCreate, SessionUpdate, Session, SessionsList, MessageCreate, MessageItem

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
    """
    Create a session for the current user.

    Supabase Python client does not support chaining select() after insert() on SyncQueryRequestBuilder.
    Therefore, we:
      1) perform the insert
      2) issue a separate select to fetch the newly created record
    """
    sb = get_supabase()
    to_insert = {"user_id": user["id"], "title": payload.title}

    # Step 1: Insert (no select chaining)
    try:
        insert_resp = sb.table("sessions").insert(to_insert).execute()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to create session: {e}")

    # Attempt to get the inserted id if returned in data (some drivers return created rows)
    created_row = None
    data_after_insert = getattr(insert_resp, "data", None) or []
    if isinstance(data_after_insert, list) and data_after_insert:
        # If rows are returned, try to use them directly
        created_row = data_after_insert[0]
    elif isinstance(data_after_insert, dict) and data_after_insert:
        created_row = data_after_insert

    # Step 2: Fetch the inserted record reliably
    # Prefer selecting by id if we have it; otherwise select the latest created for this user and title.
    try:
        if created_row and created_row.get("id"):
            sel_resp = (
                sb.table("sessions")
                .select("*")
                .eq("id", created_row["id"])
                .eq("user_id", user["id"])
                .single()
                .execute()
            )
        else:
            # Fallback: fetch the most recent session for this user with the same title
            sel_q = (
                sb.table("sessions")
                .select("*")
                .eq("user_id", user["id"])
                .order("created_at", desc=True)
            )
            # If title is None we can't filter by equality reliably; otherwise, filter by title
            if payload.title is not None:
                sel_q = sel_q.eq("title", payload.title)
            sel_resp = sel_q.limit(1).execute()
        row = getattr(sel_resp, "data", None)
        # When using .single() we get a dict; with .limit(1) we get a list
        if isinstance(row, list):
            row = row[0] if row else None
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch created session: {e}")

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

# PUBLIC_INTERFACE
@router.post(
    "/{session_id}/messages",
    response_model=MessageItem,
    status_code=status.HTTP_201_CREATED,
    summary="Post user message",
    description="Post a user message in a session. Requires at least one file uploaded for the session. Returns assistant stub response.",
)
def post_message(session_id: str, payload: MessageCreate, user=Depends(get_current_user)):
    """
    Increment 2 messages behavior:
    - Validate that session belongs to the user.
    - Accept only role='user' messages with non-empty content.
    - Precondition: at least one file exists for this session & user.
    - Persist the user message.
    - Return an assistant stub message acknowledging receipt.
    """
    sb = get_supabase()

    # Validate session ownership
    sresp = sb.table("sessions").select("id,user_id").eq("id", session_id).single().execute()
    srow = getattr(sresp, "data", None)
    if not srow or srow.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="Session not found")

    # Validate role and content
    if payload.role != "user":
        raise HTTPException(status_code=400, detail="Only role='user' is supported in this increment")
    if not payload.content or not payload.content.strip():
        raise HTTPException(status_code=400, detail="Content is required")

    # Precondition: at least one file for session
    fresp = sb.table("files").select("id").eq("user_id", user["id"]).eq("session_id", session_id).limit(1).execute()
    files_rows = getattr(fresp, "data", []) or []
    if not files_rows:
        raise HTTPException(status_code=400, detail="At least one file must be uploaded for this session before sending messages")

    # Insert user message
    try:
        (
            sb.table("messages")
            .insert({"session_id": session_id, "user_id": user["id"], "role": "user", "content": payload.content})
            .select("*")
            .single()
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save message: {e}")

    # Create assistant stub (not persisted as assistant response yet, or we can persist too)
    now_iso = datetime.now(timezone.utc).isoformat()
    assistant_msg = {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "user_id": user["id"],
        "role": "assistant",
        "content": "Thanks! File received, processing soon.",
        "created_at": now_iso,
    }
    # Optionally persist assistant stub for thread continuity
    try:
        sb.table("messages").insert({
            "id": assistant_msg["id"],
            "session_id": session_id,
            "user_id": user["id"],
            "role": "assistant",
            "content": assistant_msg["content"],
            "created_at": assistant_msg["created_at"],
        }).execute()
    except Exception:
        # Non-fatal; still return assistant stub
        pass

    return MessageItem(**assistant_msg)
