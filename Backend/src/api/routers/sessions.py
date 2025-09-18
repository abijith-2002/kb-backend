from fastapi import APIRouter, Depends, HTTPException, status
from supabase import create_client
import os
from typing import Dict, Any, List

from ..deps import get_current_user
from ..models import SessionCreate, SessionUpdate, Session, SessionsList, MessagesList, Message, MessageCreate

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
    "/{session_id}/files/{file_id}/pin",
    summary="Pin file to session",
    description="Link a file to a session by setting files.session_id. Requires both the session and file to belong to the current user. Idempotent: if already linked, returns success.",
)
def pin_file_to_session(session_id: str, file_id: str, user=Depends(get_current_user)) -> Dict[str, Any]:
    """
    Pin a file to a session (set files.session_id) with strict ownership checks.

    Steps:
    1) Validate the session exists and is owned by the current user.
    2) Validate the file exists and is owned by the current user.
    3) If file.session_id is already the requested session_id, return the current file row.
    4) Otherwise, update files.session_id = session_id and return the updated file row.

    Returns:
    - The updated file row (JSON) including id, user_id, session_id, name, storage_path, etc.
    """
    sb = get_supabase()

    # Verify session ownership
    sess_resp = (
        sb.table("sessions").select("id,user_id").eq("id", session_id).eq("user_id", user["id"]).single().execute()
    )
    session_row = getattr(sess_resp, "data", None)
    if not session_row:
        # Do not leak existence; if not owned or not found -> 404
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    # Verify file ownership
    file_resp = (
        sb.table("files").select("*").eq("id", file_id).eq("user_id", user["id"]).single().execute()
    )
    file_row = getattr(file_resp, "data", None)
    if not file_row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    # Idempotency: already pinned to this session
    current_session_id = file_row.get("session_id")
    if current_session_id == session_id:
        return file_row

    # Update the file's session_id; RLS enforces user ownership
    try:
        upd_resp = (
            sb.table("files")
            .update({"session_id": session_id})
            .eq("id", file_id)
            .eq("user_id", user["id"])
            .select("*")
            .single()
            .execute()
        )
        updated = getattr(upd_resp, "data", None)
        if not updated:
            # Could happen if RLS blocked or row missing
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found or not updated")
        return updated
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to pin file: {e}")

# PUBLIC_INTERFACE
@router.get(
    "/{session_id}/messages",
    response_model=MessagesList,
    summary="List session messages",
    description="List messages for a given session in ascending order (must belong to current user).",
)
def list_session_messages(session_id: str, user=Depends(get_current_user)) -> MessagesList:
    """
    List messages for a session owned by the current user.

    Returns messages ordered ascending by created_at.
    """
    sb = get_supabase()
    # Validate session ownership
    sess_resp = (
        sb.table("sessions")
        .select("id")
        .eq("id", session_id)
        .eq("user_id", user["id"])
        .single()
        .execute()
    )
    if not getattr(sess_resp, "data", None):
        raise HTTPException(status_code=404, detail="Session not found")

    # Fetch messages with RLS in place
    resp = (
        sb.table("messages")
        .select("*")
        .eq("session_id", session_id)
        .eq("user_id", user["id"])
        .order("created_at", desc=False)
        .execute()
    )
    rows = getattr(resp, "data", []) or []
    items: List[Message] = [Message(**r) for r in rows]
    return MessagesList(items=items)

# PUBLIC_INTERFACE
@router.post(
    "/{session_id}/messages",
    response_model=MessagesList,
    summary="Create user message (requires pinned file)",
    description="Accepts a user message only if at least one file is pinned to the session. Inserts the user message and a stub assistant response, returning both.",
)
def create_user_message(session_id: str, payload: MessageCreate, user=Depends(get_current_user)) -> MessagesList:
    """
    Create a new user message after validating:
    - Session exists and belongs to the user.
    - At least one file is pinned to the session.

    Then insert:
    - The user's message (role='user').
    - A stub assistant message acknowledging receipt.

    Returns both inserted messages ordered by created_at ascending.
    """
    if not payload.content or not payload.content.strip():
        raise HTTPException(status_code=400, detail="Message content cannot be empty")

    sb = get_supabase()

    # Validate session ownership
    sess_resp = (
        sb.table("sessions")
        .select("id")
        .eq("id", session_id)
        .eq("user_id", user["id"])
        .single()
        .execute()
    )
    if not getattr(sess_resp, "data", None):
        raise HTTPException(status_code=404, detail="Session not found")

    # Enforce at least one pinned file
    files_resp = (
        sb.table("files")
        .select("id")
        .eq("user_id", user["id"])
        .eq("session_id", session_id)
        .limit(1)
        .execute()
    )
    files = getattr(files_resp, "data", []) or []
    if not files:
        raise HTTPException(
            status_code=400,
            detail="At least one file must be pinned to this session before sending messages.",
        )

    # Insert user message
    try:
        user_msg_resp = (
            sb.table("messages")
            .insert(
                {
                    "session_id": session_id,
                    "user_id": user["id"],
                    "role": "user",
                    "content": payload.content.strip(),
                }
            )
            .select("*")
            .single()
            .execute()
        )
        user_msg_row = getattr(user_msg_resp, "data", None)
        if not user_msg_row:
            raise HTTPException(status_code=500, detail="Failed to insert user message")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to insert user message: {e}")

    # Insert stub assistant response
    try:
        assistant_msg_resp = (
            sb.table("messages")
            .insert(
                {
                    "session_id": session_id,
                    "user_id": user["id"],
                    "role": "assistant",
                    "content": "Thanks! File received, processing soon.",
                }
            )
            .select("*")
            .single()
            .execute()
        )
        assistant_msg_row = getattr(assistant_msg_resp, "data", None)
        if not assistant_msg_row:
            raise HTTPException(status_code=500, detail="Failed to insert assistant message")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to insert assistant message: {e}")

    # Return both messages ascending by created_at
    # We re-query both by IDs to preserve any DB-populated fields and ordering
    ids = [user_msg_row["id"], assistant_msg_row["id"]]
    list_resp = (
        sb.table("messages")
        .select("*")
        .in_("id", ids)
        .order("created_at", desc=False)
        .execute()
    )
    rows = getattr(list_resp, "data", []) or []
    items: List[Message] = [Message(**r) for r in rows]
    return MessagesList(items=items)
