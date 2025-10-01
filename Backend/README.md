# KnowledgeBot Backend – Supabase RLS and Files Upload Notes

This service uses Supabase for Auth, PostgREST, and Storage. RLS is enabled on `public.sessions`, `public.messages`, and `public.files`.

Why you might see “new row violates row-level security policy” on POST /files or POST /sessions:
- The database enforces RLS policies requiring `auth.uid()` to match the row’s `user_id` on insert/update/delete.
- If the backend uses an anon key or service key but does not attach the end-user JWT to the Supabase client for DB calls, PostgREST sees no user and blocks the write.

Backend behavior (fixed):
- The API constructs a Supabase client that carries the Authorization bearer token for each request and explicitly sets PostgREST auth. We call both `client.auth.set_auth(access_token)` and `client.postgrest.auth(access_token)` so DB calls run under the user’s identity and RLS `auth.uid()` is populated.
- If the Authorization header is missing or invalid, endpoints return 401 via the get_current_user dependency.

Environment variables required:
- SUPABASE_URL
- SUPABASE_ANON_KEY
- SUPABASE_SERVICE_ROLE_KEY (recommended for server side; still attach user token for RLS-aware DB calls)
- SUPABASE_JWT_SECRET (optional; enables local JWT decode path)
- SUPABASE_STORAGE_BUCKET (default: user-files)
- CORS_ORIGINS (optional, comma-separated)

Supabase dashboard checks:
1) Tables & RLS:
   - public.sessions, public.messages, public.files -> RLS enabled.
   - Policies should match migrations/001_init.sql; specifically:
     - Files insert: WITH CHECK (auth.uid() = user_id)
     - Sessions insert: WITH CHECK (auth.uid() = user_id)

2) Storage:
   - Create bucket named “user-files” or set SUPABASE_STORAGE_BUCKET accordingly.
   - Storage policies as needed. For default projects, service role may upload regardless; if using anon, add policies or rely on server/service role.

3) Keys & JWT:
   - Ensure backend has SUPABASE_SERVICE_ROLE_KEY (preferred) and SUPABASE_ANON_KEY set.
   - Frontend must send Authorization: Bearer <access_token> for protected endpoints.

Troubleshooting:
- If POST /files still returns RLS error:
  - Confirm the Authorization header is present and valid.
  - Verify that the user_id in the metadata insert equals auth.uid() from the token.
  - Check that the session belongs to the same user (the API already enforces this).
  - Re-run the migration SQL in Supabase SQL editor to ensure policies are in place.

