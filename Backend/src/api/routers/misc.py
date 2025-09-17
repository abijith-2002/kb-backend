import os
from fastapi import APIRouter, Depends
from supabase import create_client

from ..deps import get_current_user
from ..models import QuotaInfo, ModerationCheckRequest, ModerationCheckResponse, AuditLogList, AuditLogItem
from .services import moderate_text

router = APIRouter(prefix="/misc", tags=["Misc"])

def get_supabase():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        raise RuntimeError("Supabase configuration missing.")
    return create_client(url, key)

# PUBLIC_INTERFACE
@router.get("/quota", response_model=QuotaInfo, summary="Get quota", description="Return quota status for the current user.")
def get_quota(user=Depends(get_current_user)):
    sb = get_supabase()
    # storage used
    storage_used = 0
    try:
        resp = sb.table("files").select("size").eq("user_id", user["id"]).execute()
        storage_used = sum(int(r.get("size") or 0) for r in getattr(resp, "data", []) or [])
    except Exception:
        pass
    storage_limit = int(os.getenv("KB_STORAGE_LIMIT_BYTES", "2147483648"))

    messages_used = 0
    try:
        resp = sb.rpc("count_user_messages", {"p_user_id": user["id"]}).execute()
        data = getattr(resp, "data", 0)
        if isinstance(data, dict):
            messages_used = list(data.values())[0]
        elif isinstance(data, list) and data and isinstance(data[0], dict):
            messages_used = list(data[0].values())[0]
        elif isinstance(data, (int, float)):
            messages_used = int(data)
    except Exception:
        pass
    messages_limit = int(os.getenv("KB_MESSAGES_LIMIT", "1000"))

    return QuotaInfo(
        messages_used=messages_used,
        messages_limit=messages_limit,
        storage_used_bytes=storage_used,
        storage_limit_bytes=storage_limit,
    )

# PUBLIC_INTERFACE
@router.post("/moderate", response_model=ModerationCheckResponse, summary="Moderate text", description="Run moderation check on text.")
def moderate(payload: ModerationCheckRequest, user=Depends(get_current_user)):
    allowed, cats = moderate_text(payload.text)
    return ModerationCheckResponse(allowed=allowed, categories=cats)

# PUBLIC_INTERFACE
@router.get("/audit", response_model=AuditLogList, summary="Audit logs", description="List recent audit logs for the current user.")
def audit(user=Depends(get_current_user)):
    sb = get_supabase()
    try:
        resp = sb.table("audit_logs").select("*").eq("user_id", user["id"]).order("created_at", desc=True).limit(200).execute()
        items = [AuditLogItem(**r) for r in getattr(resp, "data", []) or []]
        return AuditLogList(items=items)
    except Exception:
        return AuditLogList(items=[])
