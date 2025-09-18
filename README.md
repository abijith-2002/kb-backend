# Project Repository

This is the initial README file for the project.

## Backend API Extension – Increment 2 (Chat & File Upload)

### Key Endpoints
- POST /files — Upload a .pdf, .docx, .txt, or .xlsx file (<100MB) to Supabase Storage (bucket 'documents'). Adds DB metadata row per file. JWT protected.
- GET /files — List user-owned files (optionally filter by session_id). JWT protected.
- DELETE /files/{file_id} — Delete a user-owned file (removes DB row and storage object). JWT protected.
- POST /sessions/{session_id}/files/{file_id}/pin — Pin file to chat session (updates files.session_id). JWT protected.
- GET /sessions/{session_id}/messages — List chat messages in a session. JWT protected.
- POST /sessions/{session_id}/messages — Send a user message; requires at least one pinned file. Inserts user message and a stub assistant reply. JWT protected.

Refer to the live OpenAPI at /docs when running the backend for full schemas.

### File/Message Rules
- Allowed file types: .pdf, .docx, .txt, .xlsx (validated by extension and MIME type).
- Max file size: 100 MB. Uploads exceeding 100MB are rejected.
- Files are session-scoped via pinning: Use the pin endpoint to link a file to a session before sending messages.
- Message creation requires at least one pinned file in the target session.

### Auth
All endpoints (except health) require JWT via Authorization: Bearer <token>.
Tokens are validated using SUPABASE_JWT_SECRET (local decode) or Supabase auth.get_user as a fallback.

### Supabase Storage
- Bucket required: A Supabase Storage bucket named documents must exist.
- Upload path format: {user_id}/{yyyy}/{mm}/{dd}/{uuid4}_{sanitized_filename}
- The bucket name can be customized via STORAGE_BUCKET env variable (defaults to documents).

### Environment Variables
Set these in the backend environment:
- SUPABASE_URL: Your Supabase project URL
- SUPABASE_SERVICE_ROLE_KEY: Service key for server-side storage/database access (preferred on server)
- SUPABASE_JWT_SECRET: Used to locally verify JWTs (enables offline validation)
- CORS_ORIGINS: Comma-separated list of allowed frontend origins (e.g., https://app.example.com,http://localhost:3000)
- STORAGE_BUCKET=documents: Name of the Supabase Storage bucket for uploads

Note: If SUPABASE_SERVICE_ROLE_KEY is not set, the code falls back to SUPABASE_ANON_KEY for basic flows (ensure RLS policies allow required operations).

### OpenAPI/Docs
Endpoints and models are documented in the FastAPI/OpenAPI schema. Visit /docs when the backend is running.