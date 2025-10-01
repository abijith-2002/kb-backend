import os
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt, JWTError
from supabase import create_client, Client

security = HTTPBearer(auto_error=False)

def get_supabase() -> Client:
    """Create a Supabase client using env variables.

    Reads:
    - SUPABASE_URL
    - SUPABASE_SERVICE_ROLE_KEY (preferred for server-side operations) or SUPABASE_ANON_KEY (fallback)
    """
    url = os.getenv("SUPABASE_URL")
    # Prefer service role on the server, fall back to anon key for read-only/basic flows
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        raise RuntimeError(
            "Supabase configuration missing. Ensure SUPABASE_URL and SUPABASE_ANON_KEY (or SUPABASE_SERVICE_ROLE_KEY) are set."
        )
    return create_client(url, key)

# PUBLIC_INTERFACE
def get_supabase_for_user_request(access_token: str) -> Client:
    """Create a Supabase client and attach the end-user access token for RLS-aware DB calls.

    This ensures PostgREST receives the user's JWT (auth.uid() is populated),
    so row-level security policies like `with check (auth.uid() = user_id)` succeed on inserts/updates.

    Parameters:
    - access_token: The user's Supabase access token (JWT) from Authorization header.

    Returns:
    - Supabase Client with auth set to the provided user token.
    """
    client = get_supabase()
    # Attach the user's JWT so DB/storage calls run under their identity and RLS sees auth.uid()
    try:
        # GoTrue session (auth) for SDK-managed calls
        client.auth.set_auth(access_token)
    except Exception:
        # Ignore; may still succeed via PostgREST explicit auth
        pass
    try:
        # CRITICAL: Ensure PostgREST carries the user's token so auth.uid() is populated in RLS checks
        client.postgrest.auth(access_token)
    except Exception:
        # Older SDKs may not expose postgrest.auth; ignore if unavailable
        pass
    return client

# PUBLIC_INTERFACE
def get_current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Dict[str, Any]:
    """FastAPI dependency to validate JWT and return current user info.

    Tries these strategies:
    1) Decode with SUPABASE_JWT_SECRET if provided.
    2) Fallback to supabase.auth.get_user(access_token) (network call).

    Returns a dict with at least: {"id": <user_id>, "email": <email?>}
    """
    if creds is None or not creds.scheme.lower().startswith("bearer"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    token = creds.credentials
    supabase_jwt_secret = os.getenv("SUPABASE_JWT_SECRET")

    # Strategy 1: local decode
    if supabase_jwt_secret:
        try:
            claims = jwt.decode(token, supabase_jwt_secret, algorithms=["HS256"])
            user_id = claims.get("sub") or claims.get("user_id") or claims.get("uid")
            if not user_id:
                raise HTTPException(status_code=401, detail="Invalid token: missing sub")
            return {"id": user_id, "email": claims.get("email")}
        except JWTError:
            # fall through to remote check
            pass

    # Strategy 2: call Supabase
    sb = get_supabase()
    try:
        user_resp = sb.auth.get_user(token)
        user = user_resp.user
        if not user:
            raise HTTPException(status_code=401, detail="Invalid token")
        # supabase-py returns a User object; extract fields
        return {"id": user.id, "email": getattr(user, "email", None)}
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
