import os
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from supabase import create_client, Client

from ..deps import get_current_user

router = APIRouter(prefix="/sessions", tags=["Sessions"])

def _get_supabase() -> Client:
    """Create Supabase client using env configuration."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        raise RuntimeError(
            "Supabase configuration missing. Ensure SUPABASE_URL and SUPABASE_ANON_KEY (or SUPABASE_SERVICE_ROLE_KEY) are set."
        )
    return create_client(url, key)


def _ensure_session_owned(sb: Client, session_id: str, user_id: str):
    """Verify session exists and is owned by user. Raise 403 or 404 appropriately."""
    try:
        resp_owned = (
            sb.table("sessions")
            .select("id,user_id")
            .eq("id", session_id)
            .eq("user_id", user_id)
            .single()
            .execute()
        )
        owned_row = getattr(resp_owned, "data", None)
        if owned_row:
            return
    except Exception:
        owned_row = None

    # If not owned, determine whether it exists
    try:
        resp_any = sb.table("sessions").select("id,user_id").eq("id", session_id).single().execute()
        any_row = getattr(resp_any, "data", None)
    except Exception:
        any_row = None

    if any_row is not None and any_row.get("user_id") != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden: session is not owned by user")
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")


# Request/Response models
class MessageCreateRequest(BaseModel):
    """Payload to create a new user message in a session."""
    content: str = Field(..., description="Message text content")
    pinned_file_ids: Optional[List[str]] = Field(
        default=None, description="Optional list of file IDs the user has pinned for this turn"
    )


class MessageObject(BaseModel):
    """Message row representation."""
    id: str = Field(..., description="Message ID (UUID)")
    session_id: str = Field(..., description="Associated session ID")
    user_id: str = Field(..., description="Owner user ID")
    role: str = Field(..., description="Role of the message: user|assistant|system")
    content: str = Field(..., description="Message content")
    created_at: Optional[str] = Field(None, description="Creation timestamp (ISO)")


class MessageCreateResponse(BaseModel):
    """Response after creating a message; includes user and optional assistant stub rows."""
    items: List[MessageObject] = Field(..., description="Created message rows in chronological order")


# PUBLIC_INTERFACE
@router.post(
    "/{session_id}/message",
    response_model=MessageCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Post message to session",
    description="Create a new user message in the given session (must be owned by current user). Optionally persists a stub assistant reply.",
)
def post_message(
    session_id: str,
    payload: MessageCreateRequest,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Create a user message under the specified session, validating ownership.
    Behavior:
    - Auth required (HTTP Bearer).
    - Validates session exists and is owned by the current user (403/404 otherwise).
    - Inserts a 'user' role message into public.messages.
    - Optionally inserts an 'assistant' role stub reply (static placeholder) to unblock UI while true LLM pipeline is wired.
    - Returns created messages as {"items": [user_message, assistant_stub?]}.

    Notes:
    - pinned_file_ids is accepted for future retrieval usage; not persisted here (no table). A future migration may store per-turn pins.
    """
    if not user or "id" not in user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    sb = _get_supabase()
    user_id = user["id"]

    # 1) Ensure session ownership
    _ensure_session_owned(sb, session_id, user_id)

    # 2) Insert user message
    try:
        insert_user = {
            "session_id": session_id,
            "user_id": user_id,
            "role": "user",
            "content": payload.content,
        }
        resp_user = sb.table("messages").insert(insert_user).select("*").single().execute()
        user_row = getattr(resp_user, "data", None)
        if not user_row:
            raise HTTPException(status_code=500, detail="Failed to create user message")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create user message: {e}")

    created_items: List[Dict[str, Any]] = [user_row]

    # 3) Insert assistant stub (optional; currently always creates a stub for demo)
    try:
        assistant_text = "Thanks! Processing your message with session context and pinned files (stubbed)."
        insert_assistant = {
            "session_id": session_id,
            "user_id": user_id,  # owner for RLS alignment
            "role": "assistant",
            "content": assistant_text,
        }
        resp_assistant = sb.table("messages").insert(insert_assistant).select("*").single().execute()
        assistant_row = getattr(resp_assistant, "data", None)
        if assistant_row:
            created_items.append(assistant_row)
    except Exception:
        # Best-effort stub; if it fails we still return the user message
        pass

    # Shape as response models
    items_model = [MessageObject(**{
        "id": r.get("id"),
        "session_id": r.get("session_id"),
        "user_id": r.get("user_id"),
        "role": r.get("role"),
        "content": r.get("content"),
        "created_at": r.get("created_at"),
    }) for r in created_items]

    return MessageCreateResponse(items=items_model)
