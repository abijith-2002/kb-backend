import os
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt, JWTError
from supabase import create_client, Client

security = HTTPBearer(auto_error=False)

def get_supabase() -> Client:
    """Create a Supabase client using env variables."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        raise RuntimeError("Supabase configuration missing. Ensure SUPABASE_URL and keys are set.")
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
