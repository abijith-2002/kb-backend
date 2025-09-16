import os

from fastapi import APIRouter, Depends, HTTPException
from supabase import create_client, Client
from .utils import ensure_profile
from ..models import SignupRequest, LoginRequest, TokenResponse, Profile
from ..deps import get_current_user

router = APIRouter(prefix="/auth", tags=["Auth"])

def get_supabase() -> Client:
    """Create Supabase client for auth routes using SUPABASE_URL and key(s)."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        raise RuntimeError(
            "Supabase configuration missing. Ensure SUPABASE_URL and SUPABASE_ANON_KEY (or SUPABASE_SERVICE_ROLE_KEY) are set."
        )
    return create_client(url, key)

# PUBLIC_INTERFACE
@router.post("/signup", response_model=Profile, summary="Sign up", description="Create a new user with Supabase Auth and upsert profile.")
def signup(payload: SignupRequest):
    """
    Sign up a new user using Supabase Auth and upsert a profile row into public.profiles.

    Returns the created profile (id, email, full_name).
    """
    sb = get_supabase()
    # Include user_metadata for full_name if provided
    try:
        resp = sb.auth.sign_up({
            "email": str(payload.email),
            "password": payload.password,
            "options": {
                "data": {"full_name": payload.full_name} if payload.full_name else {}
            }
        })
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Signup failed: {e}")

    user = resp.user
    if not user:
        raise HTTPException(status_code=400, detail="Signup failed: no user returned")

    # Upsert profile with service key for permission
    ensure_profile(sb, user.id, user.email, payload.full_name)

    return Profile(id=user.id, email=user.email, full_name=payload.full_name)

# PUBLIC_INTERFACE
@router.post("/login", response_model=TokenResponse, summary="Log in", description="Authenticate user via Supabase and return access token.")
def login(payload: LoginRequest):
    """
    Log in a user using Supabase Auth sign_in_with_password and return access token.
    """
    sb = get_supabase()
    try:
        resp = sb.auth.sign_in_with_password({"email": str(payload.email), "password": payload.password})
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Login failed: {e}")

    session = resp.session
    if not session or not session.access_token:
        raise HTTPException(status_code=400, detail="Login failed: no access token")
    return TokenResponse(access_token=session.access_token, token_type="bearer")

# PUBLIC_INTERFACE
@router.post("/logout", summary="Log out", description="Logout current session by invalidating on Supabase.")
def logout(user=Depends(get_current_user)):
    """
    Logout the current user session via Supabase.
    """
    sb = get_supabase()
    try:
        sb.auth.sign_out()
    except Exception:
        # Best-effort sign out
        pass
    return {"success": True}

# PUBLIC_INTERFACE
@router.get("/me", response_model=Profile, summary="Get current user profile", description="Return the current user's profile data.")
def me(user=Depends(get_current_user)):
    """
    Return the current user's profile using the ID from the validated token.
    """
    sb = get_supabase()
    # Fetch profile; if none, create a minimal one
    data = (
        sb.table("profiles")
        .select("*")
        .eq("id", user["id"])
        .limit(1)
        .execute()
    )
    rows = getattr(data, "data", []) or []
    if rows:
        row = rows[0]
        return Profile(id=row["id"], email=row.get("email"), full_name=row.get("full_name"))

    # Fallback: upsert a profile if missing (email might be None)
    ensure_profile(sb, user["id"], user.get("email"), None)
    return Profile(id=user["id"], email=user.get("email"), full_name=None)
