import os
from datetime import datetime, timezone
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from supabase import create_client, Client

from ..deps import get_current_user
from ..models import MessageCreate, MessageStubResponse

router = APIRouter(prefix="/sessions", tags=["Messages"])


def get_supabase() -> Client:
    """Create Supabase client for message routes using SUPABASE_URL and key(s)."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        raise RuntimeError(
            "Supabase configuration missing. Ensure SUPABASE_URL and SUPABASE_ANON_KEY (or SUPABASE_SERVICE_ROLE_KEY) are set."
        )
    return create_client(url, key)


# PUBLIC_INTERFACE
@router.post(
    "/{session_id}/messages",
    response_model=MessageStubResponse,
    summary="Post message to session",
    description="Accepts a user message for a session. Requires at least one file uploaded for that session. Returns an assistant stub response.",
)
def create_message(session_id: str, payload: MessageCreate, user=Depends(get_current_user)):
    """
    Create a user message with validation:
    - role must be 'user'
    - content must be non-empty
    - session must belong to current user
    - at least one file must exist for this session and user

    Persists the user message (optional at this increment) and returns an assistant stub message.
    """
    if payload.role != "user":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only 'user' role is accepted")
    if not payload.content or not payload.content.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Content is required")

    sb = get_supabase()

    # Check ownership of session
    sresp = sb.table("sessions").select("id").eq("id", session_id).eq("user_id", user["id"]).single().execute()
    if not getattr(sresp, "data", None):
        raise HTTPException(status_code=404, detail="Session not found")

    # Enforce precondition: at least one file exists for this session
    files_resp = sb.table("files").select("id").eq("session_id", session_id).eq("user_id", user["id"]).limit(1).execute()
    rows = getattr(files_resp, "data", []) or []
    if not rows:
        raise HTTPException(status_code=400, detail="At least one file must be uploaded for this session")

    # Optional: persist user message (kept simple here)
    try:
        sb.table("messages").insert(
            {
                "session_id": session_id,
                "user_id": user["id"],
                "role": "user",
                "content": payload.content.strip(),
            }
        ).execute()
    except Exception:
        # Non-fatal in this increment; proceed to stub
        pass

    # Build and return assistant stub response
    stub_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    return MessageStubResponse(
        id=stub_id,
        role="assistant",
        content="Thanks! File received, processing soon.",
        timestamp=now,
    )
