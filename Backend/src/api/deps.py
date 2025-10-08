import os
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, Request, status
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

# PUBLIC_INTERFACE
def get_supabase_user_scoped(request: Request) -> Client:
    """Return a Supabase client that impersonates the current end-user for DB calls.

    This sets the Authorization bearer on PostgREST so RLS policies using auth.uid() evaluate
    to the authenticated user's ID. It also sets the auth state on the client for completeness.
    """
    client = get_supabase()
    # Extract bearer token from incoming request headers
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth_header or not auth_header.lower().startswith("bearer "):
        # Caller should have already enforced auth, but guard anyway
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Authorization header")

    token = auth_header.split(" ", 1)[1].strip()
    try:
        # Ensure both the auth and postgrest clients carry the user token
        client.auth.set_auth(token)
        # Newer supabase-py exposes postgrest auth to forward token
        if hasattr(client, "postgrest") and hasattr(client.postgrest, "auth"):
            client.postgrest.auth(token)
        # Defensive: set header if supported (SDK variations)
        if hasattr(client, "postgrest") and hasattr(client.postgrest, "client"):
            headers = getattr(client.postgrest.client, "headers", None)
            if isinstance(headers, dict):
                headers["Authorization"] = f"Bearer {token}"
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to scope Supabase client to user: {e}")
    return client
