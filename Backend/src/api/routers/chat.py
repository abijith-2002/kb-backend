import os

from fastapi import APIRouter, Depends, HTTPException, Path
from supabase import create_client

from ..deps import get_current_user
from ..models import MessageCreate, MessagesList, MessageItem, AnswerRequest, AnswerResponse, SearchRequest, SearchResponse, SearchResult
from .services import (
    get_chroma,
    query_embeddings,
    answer_with_gemini,
    ensure_audit_log,
    check_quota,
    moderate_text,
)

router = APIRouter(prefix="/chat", tags=["Chat"])

def get_supabase():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        raise RuntimeError("Supabase configuration missing.")
    return create_client(url, key)

# PUBLIC_INTERFACE
@router.get("/{session_id}/messages", response_model=MessagesList, summary="List messages", description="List messages in a session for the current user.")
def list_messages(session_id: str = Path(..., description="Session ID"), user=Depends(get_current_user)):
    sb = get_supabase()
    # Ownership check via join in policies; we also filter by user_id and session_id
    resp = sb.table("messages").select("*").eq("session_id", session_id).eq("user_id", user["id"]).order("created_at").execute()
    items = [MessageItem(**r) for r in getattr(resp, "data", []) or []]
    return MessagesList(items=items)

# PUBLIC_INTERFACE
@router.post("/{session_id}/messages", response_model=MessageItem, summary="Create message", description="Create a user message in a session.")
def create_message(session_id: str, payload: MessageCreate, user=Depends(get_current_user)):
    sb = get_supabase()
    # simple moderation gate
    allowed, cats = moderate_text(payload.content)
    if not allowed:
        raise HTTPException(status_code=400, detail=f"Message blocked by moderation: {', '.join(cats)}")

    check_quota(sb, user["id"], 0)

    try:
        row = sb.table("messages").insert({
            "session_id": session_id,
            "user_id": user["id"],
            "role": "user",
            "content": payload.content,
        }).select("*").single().execute().data
        ensure_audit_log(sb, user["id"], "message_create", {"session_id": session_id, "message_id": row.get("id")})
        return MessageItem(**row)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to create message: {e}")

# PUBLIC_INTERFACE
@router.post("/{session_id}/answer", response_model=AnswerResponse, summary="Answer question", description="Run RAG: retrieve with Chroma and answer with Gemini using session-scoped corpus.")
def answer_question(session_id: str, payload: AnswerRequest, user=Depends(get_current_user)):
    sb = get_supabase()

    # Save the user question as a message
    allowed, cats = moderate_text(payload.query)
    if not allowed:
        raise HTTPException(status_code=400, detail=f"Question blocked by moderation: {', '.join(cats)}")
    check_quota(sb, user["id"], 0)
    try:
        sb.table("messages").insert({
            "session_id": session_id,
            "user_id": user["id"],
            "role": "user",
            "content": payload.query,
        }).execute()
    except Exception:
        pass

    # Retrieve
    hits = query_embeddings(get_chroma(), user["id"], session_id, payload.query, top_k=payload.top_k)
    context = [h["text"] for h in hits]
    # Generate
    answer = answer_with_gemini(context, payload.query, temperature=payload.temperature)

    # Save assistant message
    try:
        resp = sb.table("messages").insert({
            "session_id": session_id,
            "user_id": user["id"],
            "role": "assistant",
            "content": answer,
        }).select("*").single().execute()
        msg_row = getattr(resp, "data", None)
        ensure_audit_log(sb, user["id"], "answer_create", {"session_id": session_id, "assistant_message_id": (msg_row or {}).get("id")})
    except Exception:
        pass

    return AnswerResponse(
        answer=answer,
        sources=[{"file_id": h.get("file_id"), "chunk_index": h.get("chunk_index"), "score": h.get("score"), "text": h.get("text")} for h in hits],
    )

# PUBLIC_INTERFACE
@router.post("/{session_id}/search", response_model=SearchResponse, summary="Semantic search", description="Search within the session-scoped corpus.")
def semantic_search(session_id: str, payload: SearchRequest, user=Depends(get_current_user)):
    hits = query_embeddings(get_chroma(), user["id"], session_id, payload.query, top_k=payload.top_k)
    results = [SearchResult(file_id=h.get("file_id"), text=h.get("text"), score=h.get("score")) for h in hits]
    return SearchResponse(results=results)
