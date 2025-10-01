import os
import logging
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt, JWTError
from supabase import create_client, Client

# Initialize a module-level logger for Supabase-related diagnostics
logger = logging.getLogger("kb_backend.supabase")
if not logger.handlers:
    # BasicConfig only impacts root logger if no handlers exist; safe default here
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
try:
    logger.setLevel(getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO))
except Exception:
    # Fallback if env var is malformed
    logger.setLevel(logging.INFO)

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
    if not access_token:
        # No token; warn, because RLS-protected calls may fail or appear as empty results
        logger.warning("get_supabase_for_user_request: No access token provided; RLS may fail.")
        return client

    # Attach the user's JWT so DB/storage calls run under their identity and RLS sees auth.uid()
    postgrest_auth_applied = False
    try:
        # GoTrue session (auth) for SDK-managed calls
        client.auth.set_auth(access_token)
    except Exception:
        # Ignore; may still succeed via PostgREST explicit auth
        logger.debug("get_supabase_for_user_request: client.auth.set_auth failed or unavailable.")

    try:
        # CRITICAL: Ensure PostgREST carries the user's token so auth.uid() is populated in RLS checks
        client.postgrest.auth(access_token)
        postgrest_auth_applied = True
    except Exception:
        # Older SDKs may not expose postgrest.auth; ignore if unavailable
        logger.warning("get_supabase_for_user_request: client.postgrest.auth unavailable on this SDK.")

    logger.debug(
        "Supabase user client created. postgrest_auth_applied=%s",
        postgrest_auth_applied,
    )
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


# PUBLIC_INTERFACE
def get_bearer_token_from_request(request: Optional[Request]) -> Optional[str]:
    """Extract the Bearer token from the Authorization header of a FastAPI Request.

    This helper is robust to header case and ensures only the token value is returned.
    Returns None if header is missing or malformed.
    """
    if not request:
        return None
    auth_header = request.headers.get("authorization")
    if not auth_header:
        return None
    if not auth_header.lower().startswith("bearer "):
        return None
    return auth_header.split(" ", 1)[1].strip() or None


# PUBLIC_INTERFACE
def extract_user_id_from_token_unverified(token: Optional[str]) -> Optional[str]:
    """Extract user id (sub) from a JWT without verifying its signature.

    This is safe for diagnostics as we do not trust or authorize based on this value.
    We do not log or return the full token.

    Returns:
    - user_id string if present, else None.
    """
    if not token:
        return None
    try:
        claims = jwt.get_unverified_claims(token)
        return claims.get("sub") or claims.get("user_id") or claims.get("uid")
    except Exception:
        return None


# PUBLIC_INTERFACE
def get_supabase_debug_snapshot(client: Client) -> Dict[str, Any]:
    """Return a safe snapshot of the Supabase client's PostgREST auth state.

    The snapshot avoids leaking secrets; it only indicates whether Authorization is set
    and basic configuration flags, not the actual token value.
    """
    snapshot: Dict[str, Any] = {
        "url_configured": bool(os.getenv("SUPABASE_URL")),
        "using_service_role_key": bool(os.getenv("SUPABASE_SERVICE_ROLE_KEY")),
        "using_anon_key": bool(os.getenv("SUPABASE_ANON_KEY")),
        "postgrest_authorization_bearer_set": None,
        "postgrest_authorization_preview": None,
    }
    try:
        postgrest_client = getattr(client, "postgrest", None)
        headers = {}
        # Try multiple candidate attribute names to accommodate SDK changes
        for candidate in ("headers", "_headers", "default_headers"):
            try:
                hdrs = getattr(postgrest_client, candidate, None)
                if hdrs:
                    headers = hdrs
                    break
            except Exception:
                continue
        auth_hdr = None
        if isinstance(headers, dict):
            auth_hdr = headers.get("Authorization") or headers.get("authorization")
        if auth_hdr:
            snapshot["postgrest_authorization_bearer_set"] = "Bearer " in auth_hdr
            # Only include a minimal preview length, not the token itself
            snapshot["postgrest_authorization_preview"] = f"{auth_hdr[:16]}...len={len(auth_hdr)}"
        else:
            snapshot["postgrest_authorization_bearer_set"] = False
    except Exception:
        # If introspection fails, leave values as None
        pass
    return snapshot
