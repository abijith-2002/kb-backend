# KnowledgeBot Backend API Endpoints

## Table of Contents
- [Overview](#overview)
- [Authentication and Security](#authentication-and-security)
- [Routers and Endpoints](#routers-and-endpoints)
  - [Root (main.py)](#root-mainpy)
  - [Auth Router (routers/auth.py)](#auth-router-routersauthpy)
  - [Sessions Router (routers/sessionspy)](#sessions-router-routerssessionspy)
  - [Files Router (routers/filespy)](#files-router-routersfilespy)
- [Models](#models)
- [Status Codes](#status-codes)
- [Notes on External Services](#notes-on-external-services)
- [Changelog Notes](#changelog-notes)
- [OpenAPI Cross-Reference](#openapi-cross-reference)

## Overview
This document lists all FastAPI endpoints exposed by the backend, grouped by the router/module where they are defined. For each endpoint, it summarizes the HTTP methods, path, request and response models, parameters, status codes, authentication/authorization requirements, and a brief description. Where available, example request/response payloads are provided. The backend leverages Supabase for authentication, database (PostgREST) access, and will interface with Supabase Storage for files.

Source files scanned:
- src/api/main.py
- src/api/deps.py
- src/api/models.py
- src/api/routers/auth.py
- src/api/routers/sessions.py
- src/api/routers/files.py
- interfaces/openapi.json (static snapshot packaged with the repository)

## Authentication and Security
- Protected endpoints require an Authorization header: `Authorization: Bearer <JWT>`.
- Tokens are validated by the dependency in src/api/deps.py:
  - Attempts local JWT decode with SUPABASE_JWT_SECRET when available.
  - Falls back to `supabase.auth.get_user(token)` to validate remotely.
- Most business endpoints require authentication via `Depends(get_current_user)`.
- The application integrates with Supabase; some endpoints operate on RLS-protected tables and rely on forwarding the user identity.

## Routers and Endpoints

### Root (main.py)
- GET `/`
  - Summary: Health Check
  - Description: Health check endpoint to verify the service is running.
  - Auth: None (public)
  - Request Body: None
  - Query Params: None
  - Path Params: None
  - Response Model: HealthResponse
    - Example:
      {
        "message": "Healthy"
      }
  - Status Codes: 200

### Auth Router (routers/auth.py)
Base prefix: `/auth`
Tags: Auth

- POST `/auth/signup`
  - Summary: Sign up
  - Description: Create a new user with Supabase Auth and upsert profile.
  - Auth: None (public)
  - Request Body Model: SignupRequest
    - {
      "email": "user@example.com",
      "password": "string",
      "full_name": "Optional Name"
    }
  - Response Model: Profile
    - {
      "id": "uuid",
      "email": "user@example.com",
      "full_name": "Optional Name"
    }
  - Status Codes: 200, 400 (on failure), 422 (validation)
  - External Services: Supabase Auth (sign_up), Supabase DB (profiles upsert via utility)

- POST `/auth/login`
  - Summary: Log in
  - Description: Authenticate user via Supabase and return access token.
  - Auth: None (public)
  - Request Body Model: LoginRequest
    - {
      "email": "user@example.com",
      "password": "string"
    }
  - Response Model: TokenResponse
    - {
      "access_token": "<jwt>",
      "token_type": "bearer"
    }
  - Status Codes: 200, 400 (on failure), 422 (validation)
  - External Services: Supabase Auth (sign_in_with_password)

- POST `/auth/logout`
  - Summary: Log out
  - Description: Logout current session by invalidating on Supabase (best-effort).
  - Auth: Required (Bearer). Uses Depends(get_current_user).
  - Request Body: None
  - Response: {"success": true}
  - Status Codes: 200
  - External Services: Supabase Auth (sign_out)

- GET `/auth/me`
  - Summary: Get current user profile
  - Description: Return the current user's profile data (creates minimal profile if missing).
  - Auth: Required (Bearer). Uses Depends(get_current_user).
  - Request Body: None
  - Response Model: Profile
    - {
      "id": "uuid",
      "email": "user@example.com",
      "full_name": "Optional Name"
    }
  - Status Codes: 200
  - External Services: Supabase DB (profiles)

### Sessions Router (routers/sessions.py)
Base prefix: `/sessions`
Tags: Sessions

- GET `/sessions`
  - Summary: List sessions
  - Description: List all sessions for the current user, ordered by most recent.
  - Auth: Required (Bearer). Uses Depends(get_current_user).
  - Request Body: None
  - Response Model: SessionsList
    - {
      "items": [
        {
          "id": "uuid",
          "user_id": "uuid",
          "title": "Optional title",
          "created_at": "ISO",
          "updated_at": "ISO"
        }
      ]
    }
  - Status Codes: 200
  - External Services: Supabase DB (sessions)

- POST `/sessions`
  - Summary: Create session
  - Description: Create a new session for the current user.
  - Auth: Required (Bearer). Uses Depends(get_current_user).
  - Request Body Model: SessionCreate
    - {
      "title": "Optional title"
    }
  - Response Model: Session
    - {
      "id": "uuid",
      "user_id": "uuid",
      "title": "Optional title",
      "created_at": "ISO",
      "updated_at": "ISO"
    }
  - Status Codes: 201, 400 (creation failure), 422 (validation)
  - External Services: Supabase DB (sessions)

- GET `/sessions/{session_id}`
  - Summary: Get session
  - Description: Fetch a session by ID (must belong to current user).
  - Auth: Required (Bearer). Uses Depends(get_current_user).
  - Path Params:
    - session_id: string (UUID)
  - Response Model: Session
  - Status Codes: 200, 404 (not found), 422 (validation)
  - External Services: Supabase DB (sessions)

- PATCH `/sessions/{session_id}`
  - Summary: Update session
  - Description: Update a session's title (must belong to current user).
  - Auth: Required (Bearer). Uses Depends(get_current_user).
  - Path Params:
    - session_id: string (UUID)
  - Request Body Model: SessionUpdate
    - {
      "title": "New title or null"
    }
  - Response Model: Session
  - Status Codes: 200, 400 (update failure), 404 (not found), 422 (validation)
  - External Services: Supabase DB (sessions)

- DELETE `/sessions/{session_id}`
  - Summary: Delete session
  - Description: Delete a session (must belong to current user). Returns no content.
  - Auth: Required (Bearer). Uses Depends(get_current_user).
  - Path Params:
    - session_id: string (UUID)
  - Response: No content
  - Status Codes: 204
  - External Services: Supabase DB (sessions)

### Files Router (routers/files.py)
Base prefix: `/files`
Tags: Files

Note: These endpoints are stubs in the current codebase.

- GET `/files`
  - Summary: List files (stub)
  - Description: Protected stub endpoint for listing files.
  - Auth: Required (Bearer). Uses Depends(get_current_user).
  - Request Body: None
  - Response: {"items": [], "note": "Files endpoints are stubs. To be implemented."}
  - Status Codes: 200
  - External Services: Intended to use Supabase DB/Storage (future)

- POST `/files`
  - Summary: Upload file (stub)
  - Description: Protected stub endpoint for uploading a file.
  - Auth: Required (Bearer). Uses Depends(get_current_user).
  - Request Body: Not yet defined (future: multipart/form-data).
  - Response: {"success": true, "note": "Upload stub. To be implemented."}
  - Status Codes: 200
  - External Services: Intended to use Supabase Storage and DB (future)

- DELETE `/files/{file_id}`
  - Summary: Delete file (stub)
  - Description: Protected stub endpoint for deleting a file.
  - Auth: Required (Bearer). Uses Depends(get_current_user).
  - Path Params:
    - file_id: string (UUID)
  - Response: {"success": true, "note": "Delete stub for {file_id}. To be implemented."}
  - Status Codes: 200, 422 (validation)
  - External Services: Intended to use Supabase Storage and DB (future)

## Models
Defined in src/api/models.py:

- HealthResponse
  - message: str

- SignupRequest
  - email: EmailStr
  - password: str
  - full_name: Optional[str]

- LoginRequest
  - email: EmailStr
  - password: str

- TokenResponse
  - access_token: str
  - token_type: str = "bearer"

- Profile
  - id: str
  - email: Optional[EmailStr]
  - full_name: Optional[str]

- SessionCreate
  - title: Optional[str]

- SessionUpdate
  - title: Optional[str]

- Session
  - id: str
  - user_id: str
  - title: Optional[str]
  - created_at: Optional[str]
  - updated_at: Optional[str]

- SessionsList
  - items: List[Session]

## Status Codes
- 200: Successful response (typical for reads and simple actions).
- 201: Resource created (POST /sessions).
- 204: No content (DELETE /sessions/{session_id}).
- 400: Bad request or operation failed (auth or sessions operations on error).
- 401: Not authenticated (returned by dependencies when missing/invalid token).
- 404: Resource not found (e.g., sessions).
- 422: Validation error for invalid payloads or parameters.

## Notes on External Services
- Supabase Auth is used for signup, login, logout, and for validating current user identity.
- Supabase Database is used for sessions and profiles (via the Supabase Python client).
- Files endpoints are currently stubs but are intended to integrate with Supabase Storage and the files table based on future increments.
- If SUPABASE_URL / keys are missing, endpoints that require Supabase will raise clear errors.

## Changelog Notes
- Sessions router includes endpoints for CRUD with robust handling of supabase-py 2.x response shapes and fallbacks where needed.
- Files router currently contains stubs; future increments will add multipart handling, storage integration, and deletion semantics.
- Auth router includes profile upsert utility to maintain consistency with database state.

## OpenAPI Cross-Reference
A static OpenAPI snapshot is included at Backend/interfaces/openapi.json. The documented endpoints, models, and tags align with that snapshot:
- Root health check: GET /
- Auth: /auth/signup, /auth/login, /auth/logout, /auth/me
- Sessions: /sessions (GET, POST), /sessions/{session_id} (GET, PATCH, DELETE)
- Files: /files (GET, POST), /files/{file_id} (DELETE)

If the application is running, you can also access the live OpenAPI at /openapi.json and Swagger UI at /docs on the running backend service. This document reflects the current repository code.
