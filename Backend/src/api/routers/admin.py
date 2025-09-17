import os
from fastapi import APIRouter, Depends
from supabase import create_client
from ..deps import get_current_user
from ..models import AdminStats

router = APIRouter(prefix="/admin", tags=["Admin"])

def get_supabase():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        raise RuntimeError("Supabase configuration missing.")
    return create_client(url, key)

# PUBLIC_INTERFACE
@router.get("/stats", response_model=AdminStats, summary="Admin stats", description="Basic admin monitoring stats. Requires admin user.")
def stats(user=Depends(get_current_user)):
    # Simple admin check by email whitelist
    admin_emails = [e.strip() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()]
    if user.get("email") not in admin_emails:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Forbidden")

    sb = get_supabase()
    def count(table):
        try:
            resp = sb.table(table).select("id", count="exact").execute()
            return int(resp.count or 0)
        except Exception:
            return 0

    total_users = 0
    try:
        # Not directly accessible; keep 0 or build a mirror table
        total_users = 0
    except Exception:
        pass

    return AdminStats(
        total_users=total_users,
        total_sessions=count("sessions"),
        total_files=count("files"),
        total_messages=count("messages"),
        last_24h_requests=0,  # Could be fed by audit_logs aggregate
    )
