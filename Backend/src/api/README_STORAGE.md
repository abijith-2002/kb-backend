# Storage Configuration

The Files API uses Supabase Storage to store uploaded user documents.

Environment variables:
- SUPABASE_URL: Supabase project URL (required)
- SUPABASE_ANON_KEY or SUPABASE_SERVICE_ROLE_KEY: API key (required; service role preferred on backend)
- SUPABASE_STORAGE_BUCKET: Name of the storage bucket where files are uploaded. Defaults to "user-content" if not provided.

Storage object path pattern:
- user/{user_id}/session/{session_id}/{filename}

Ensure you have created the storage bucket in Supabase Dashboard and configured appropriate RLS/policies if needed. The backend uses the service role to write objects and regular JWT for DB RLS-enforced metadata operations.
