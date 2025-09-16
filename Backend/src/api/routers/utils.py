from typing import Optional
from supabase import Client

def ensure_profile(sb: Client, user_id: str, email: Optional[str], full_name: Optional[str]):
    """Upsert a profile row in public.profiles for the given user."""
    try:
        sb.table("profiles").upsert(
            {
                "id": user_id,
                "email": email,
                "full_name": full_name,
            },
            on_conflict="id",
        ).execute()
    except Exception:
        # Best effort; rely on optional DB trigger otherwise
        pass
