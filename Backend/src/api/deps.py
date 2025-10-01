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

    # CRITICAL: Ensure PostgREST carries the user's token so auth.uid() is populated in RLS checks
    try:
        client.postgrest.auth(access_token)
        postgrest_auth_applied = True
    except Exception:
        # Older SDKs may not expose postgrest.auth; ignore if unavailable
        logger.warning("get_supabase_for_user_request: client.postgrest.auth unavailable on this SDK.")

    # Defensive fallback for SDK variations: write the header directly if possible
    try:
        postgrest_client = getattr(client, "postgrest", None)
        if postgrest_client is not None:
            for candidate in ("headers", "_headers", "default_headers"):
                hdrs = getattr(postgrest_client, candidate, None)
                if isinstance(hdrs, dict):
                    # Set or overwrite Authorization header
                    hdrs["Authorization"] = f"Bearer {access_token}"
                    try:
                        setattr(postgrest_client, candidate, hdrs)  # In case the attribute needs explicit re-assignment
                    except Exception:
                        pass
                    break
    except Exception:
        # Best-effort; continue
        logger.debug("get_supabase_for_user_request: direct header assignment fallback failed.")

    logger.debug(
        "Supabase user client created. postgrest_auth_applied=%s",
        postgrest_auth_applied,
    )
    return client


# PUBLIC_INTERFACE
def get_supabase_user_scoped(request: Optional[Request]) -> Client:
    """Return a Supabase client configured to act as the current end user.

    It extracts the access token from the incoming Request's Authorization header,
    then sets both the GoTrue and PostgREST layers:

    - client.auth.set_auth(access_token)
    - client.postgrest.auth(access_token)

    Additionally, it defensively sets the Authorization: Bearer <token> header
    directly on the PostgREST client if the SDK variation doesn't expose .auth().

    If no token is present, a generic client is returned (RLS-protected queries may fail).
    """
    token = get_bearer_token_from_request(request)
    return get_supabase_for_user_request(token) if token else get_supabase()


# PUBLIC_INTERFACE
def get_current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(security),
    request: Optional[Request] = None,
) -> Dict[str, Any]:
    """FastAPI dependency to validate JWT and return current user info.

    Tries these strategies:
    1) Decode with SUPABASE_JWT_SECRET if provided.
    2) Fallback to supabase.auth.get_user(access_token) (network call).

    Side-effect:
    - Stores the raw access token on request.state.user_token if Request is available
      so downstream helpers can reuse it.

    Returns a dict with at least: {"id": <user_id>, "email": <email?>}
    """
    if creds is None or not creds.scheme.lower().startswith("bearer"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    token = creds.credentials

    # Stash token on request.state for helpers if Request is available
    try:
        if request is not None:
            setattr(request.state, "user_token", token)
    except Exception:
        pass

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
    It also falls back to request.state.user_token if present (populated by get_current_user).
    Returns None if header is missing or malformed.
    """
    if not request:
        return None
    # Primary: Authorization header
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
        if token:
            return token
    # Fallback: request.state.user_token if previously set by get_current_user
    try:
        state_token = getattr(request.state, "user_token", None)
        if state_token:
            return state_token
    except Exception:
        pass
    return None


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
