from fastapi import APIRouter, Depends, HTTPException, status
from supabase import create_client
import os
from datetime import datetime, timezone
import uuid

from ..deps import get_current_user
from ..models import (
    SessionCreate,
    SessionUpdate,
    Session,
    SessionsList,
    MessageCreate,
    MessageItem,
    SessionWithMessages,
)

router = APIRouter(prefix="/sessions", tags=["Sessions"])


def get_supabase():
    """Create Supabase client for session routes."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        raise RuntimeError(
            "Supabase configuration missing. Ensure SUPABASE_URL and SUPABASE_ANON_KEY are set."
        )
    return create_client(url, key)


# ---------------------
# List sessions
# ---------------------
@router.get("", response_model=SessionsList, summary="List sessions")
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


# ---------------------
# Create session
# ---------------------
@router.post("", response_model=Session, status_code=201, summary="Create session")
def create_session(payload: SessionCreate, user=Depends(get_current_user)):
    sb = get_supabase()
    user_id = user.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    to_insert = {"user_id": user_id, "title": payload.title}
    print(user_id)
    try:
        insert_resp = sb.table("sessions").insert(to_insert).execute()
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to create session: {e}")

    # Fetch inserted row
    data_after_insert = getattr(insert_resp, "data", []) or []
    if isinstance(data_after_insert, list) and data_after_insert:
        row = data_after_insert[0]
    else:
        sel_resp = (
            sb.table("sessions")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = getattr(sel_resp, "data", []) or []
        row = rows[0] if rows else None

    if not row:
        raise HTTPException(status_code=400, detail="Failed to create session")

    return Session(**row)


# ---------------------
# Get session by ID
# ---------------------
@router.get(
    "/{session_id}",
    response_model=SessionWithMessages,
    summary="Get session",
    description="Return a session metadata along with its messages array for the current user."
)
def get_session(session_id: str, user=Depends(get_current_user)):
    """
    Retrieve a session by ID (owned by the current user) and include all messages
    associated with that session in chronological order.
    """
    sb = get_supabase()
    # Fetch session ensuring ownership
    s_resp = (
        sb.table("sessions")
        .select("*")
        .eq("id", session_id)
        .eq("user_id", user["id"])
        .single()
        .execute()
    )
    session_row = getattr(s_resp, "data", None)
    if not session_row:
        raise HTTPException(status_code=404, detail="Session not found")

    # Fetch messages for this session (RLS ensures same user access), oldest first
    m_resp = (
        sb.table("messages")
        .select("*")
        .eq("session_id", session_id)
        .order("created_at", desc=False)
        .execute()
    )
    message_rows = getattr(m_resp, "data", []) or []
    messages = [MessageItem(**m) for m in message_rows]

    return SessionWithMessages(
        id=session_row["id"],
        user_id=session_row["user_id"],
        title=session_row.get("title"),
        created_at=session_row.get("created_at"),
        updated_at=session_row.get("updated_at"),
        messages=messages,
    )


# ---------------------
# Update session
# ---------------------
@router.patch("/{session_id}", response_model=Session, summary="Update session")
def update_session(session_id: str, payload: SessionUpdate, user=Depends(get_current_user)):
    sb = get_supabase()
    updates = {}
    if payload.title is not None:
        updates["title"] = payload.title

    if not updates:
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


# ---------------------
# Delete session
# ---------------------
@router.delete("/{session_id}", status_code=204, summary="Delete session")
def delete_session(session_id: str, user=Depends(get_current_user)):
    sb = get_supabase()
    sb.table("sessions").delete().eq("id", session_id).eq("user_id", user["id"]).execute()
    return


# ---------------------
# Post message
# ---------------------
@router.post(
    "/{session_id}/messages",
    response_model=MessageItem,
    status_code=status.HTTP_201_CREATED,
    summary="Post user message",
)
def post_message(session_id: str, payload: MessageCreate, user=Depends(get_current_user)):
    sb = get_supabase()

    # Validate session ownership
    sresp = sb.table("sessions").select("id,user_id").eq("id", session_id).single().execute()
    srow = getattr(sresp, "data", None)
    if not srow or srow.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="Session not found")

    if payload.role != "user" or not payload.content or not payload.content.strip():
        raise HTTPException(status_code=400, detail="Invalid message content")

    # Ensure at least one file exists
    fresp = sb.table("files").select("id").eq("user_id", user["id"]).eq("session_id", session_id).limit(1).execute()
    files_rows = getattr(fresp, "data", []) or []
    if not files_rows:
        raise HTTPException(status_code=400, detail="At least one file must be uploaded for this session")

    # Insert user message
    try:
        sb.table("messages").insert({
            "session_id": session_id,
            "user_id": user["id"],
            "role": "user",
            "content": payload.content
        }).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save message: {e}")

    # Return assistant stub
    now_iso = datetime.now(timezone.utc).isoformat()
    assistant_msg = {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "user_id": user["id"],
        "role": "assistant",
        "content": "Thanks! File received, processing soon.",
        "created_at": now_iso,
    }

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
        pass  # Non-fatal; still return assistant stub

    return MessageItem(**assistant_msg)
