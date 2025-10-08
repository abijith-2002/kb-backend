# Supabase RLS Fix Plan

## Context and Problem Statement
The backend is a FastAPI service using Supabase for Auth and PostgREST-backed database access. Current behavior indicates RLS violations (e.g., 42501 “new row violates row-level security policy”) on POST /sessions when the end-user identity is not properly forwarded to PostgREST or when policies are not aligned with actual access patterns. Typical root causes:
- The backend created a Supabase client with a service/anon key but did not forward the end-user JWT to PostgREST, so auth.uid() was null in RLS.
- The insert body did not set user_id to the authenticated user’s ID, failing WITH CHECK conditions.
- Missing or mismatched RLS policies or disabled RLS on tables.

In this codebase:
- RLS and policies already exist in kb-backend/migrations/001_init.sql for profiles, sessions, messages, and files.
- The server includes a user-scoped Supabase client (get_supabase_user_scoped) that forwards the user’s JWT to PostgREST to ensure auth.uid() is populated.

## Objectives
- Ensure inserts, reads, updates, and deletes succeed for authenticated users while RLS constrains data by ownership.
- Maintain least-privilege access: clients never receive the service role key; end-user JWT is forwarded to PostgREST by the backend per request.
- Keep operations auditable and secure, and align policies with the application’s desired access patterns.

## Affected Resources
- Tables: sessions (primary focus), plus profiles, messages, and files already modeled in migrations.
- Services/Environment:
  - SUPABASE_URL
  - SUPABASE_ANON_KEY (frontend/browser only)
  - SUPABASE_SERVICE_ROLE_KEY (server only)
  - SUPABASE_JWT_SECRET (optional; for local JWT verification by backend)
  - CORS_ORIGINS (optional)
- Backend code paths:
  - Backend/src/api/deps.py (user-scoped client and auth)
  - Backend/src/api/routers/sessions.py (CRUD using user-scoped client)

## Application-Level Fix
To satisfy RLS policies, PostgREST must evaluate auth.uid() to the calling user’s UUID. The backend must:
- Extract the Authorization: Bearer <access_token> header from the incoming request.
- Initialize Supabase client with server-side key (service or anon).
- Forward the access token to both the auth and postgrest sub-clients so DB calls are evaluated under the user identity.

In code (already implemented in src/api/deps.py):
- client.auth.set_auth(token)
- client.postgrest.auth(token)
- Additionally, set the Authorization header on the underlying postgrest client when available.

Additionally:
- On inserts to user-owned tables such as sessions, set user_id to the validated user’s id from the JWT.
- Avoid exposing service role key to any client or browser. Only the backend may use service role.

## RLS Policy Design
General pattern:
- Enable RLS on tables.
- Define policies to allow only owners to select/insert/update/delete.
- For inserts/updates: WITH CHECK (auth.uid() = user_id)
- For selects/updates/deletes: USING (auth.uid() = user_id)
- Optionally add an admin/service-bypass for server-maintenance tasks (e.g., with role claims), but do not expose to clients.

Sessions policies (as in migrations/001_init.sql):
- RLS enabled on public.sessions
- SELECT USING (auth.uid() = user_id)
- INSERT WITH CHECK (auth.uid() = user_id)
- UPDATE USING and WITH CHECK (auth.uid() = user_id)
- DELETE USING (auth.uid() = user_id)

Profiles, messages, files:
- Profiles: id equals auth.uid() rules for CRUD
- Messages: restricted by existence of session with sessions.user_id = auth.uid()
- Files: CRUD constrained to user_id = auth.uid()

Ensure required extensions are present:
- pgcrypto for gen_random_uuid() (enabled in migration).

Example policy SQL for sessions:
```sql
alter table public.sessions enable row level security;

drop policy if exists "Sessions: owners can select" on public.sessions;
create policy "Sessions: owners can select"
  on public.sessions for select
  using (auth.uid() = user_id);

drop policy if exists "Sessions: owners can insert" on public.sessions;
create policy "Sessions: owners can insert"
  on public.sessions for insert
  with check (auth.uid() = user_id);

drop policy if exists "Sessions: owners can update" on public.sessions;
create policy "Sessions: owners can update"
  on public.sessions for update
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

drop policy if exists "Sessions: owners can delete" on public.sessions;
create policy "Sessions: owners can delete"
  on public.sessions for delete
  using (auth.uid() = user_id);
```

Optional admin bypass (server role only):
- Use Supabase’s service role for administrative maintenance via RPC or maintenance scripts on the server, not from end-users.
- If you require a custom admin role from clients, add a claim and separate policies carefully; otherwise, prefer server-only service role usage.

## SQL Migration Examples
For sessions (already provided in kb-backend/migrations/001_init.sql):
- Table definition with user_id referencing auth.users(id)
- Indexes on user_id and created_at
- Trigger to maintain updated_at
- RLS enablement and owner-only policies

Indexes:
```sql
create index if not exists idx_sessions_user_id on public.sessions (user_id);
create index if not exists idx_sessions_created_at on public.sessions (created_at);
```

If adding similar patterns for other user-owned tables (messages/files) or future tables, reuse the same WITH CHECK/USING patterns and ensure foreign keys and ownership columns are indexed.

## JWT and Client Configuration Approach
- Frontend:
  - Use SUPABASE_URL and SUPABASE_ANON_KEY only.
  - Manage the user’s authenticated session via Supabase Auth.
  - Send Authorization: Bearer <access_token> with requests to the backend.
- Backend:
  - Use SUPABASE_SERVICE_ROLE_KEY if available (preferred) or SUPABASE_ANON_KEY, server-side only.
  - Validate JWT in get_current_user:
    - Prefer local decode with SUPABASE_JWT_SECRET when available (faster); fallback to sb.auth.get_user(token).
  - For DB calls, always scope the Supabase client to the user token:
    - client.auth.set_auth(token)
    - client.postgrest.auth(token)
  - Set user_id fields explicitly from the token’s subject (sub).

## Testing Strategy
Manual (curl) positive path:
1) Obtain an access token:
   - From frontend login or via POST /auth/login in this backend.
2) Create a session:
```bash
TOKEN="eyJ..."  # a valid Supabase access token (JWT)
curl -s -X POST "$BACKEND_URL/sessions" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title": "My first session"}' | jq
```
Expected:
- 201 Created with a session JSON containing user_id equal to the token’s user id.
- No RLS errors.

3) List sessions:
```bash
curl -s -X GET "$BACKEND_URL/sessions" \
  -H "Authorization: Bearer $TOKEN" | jq
```
Expected:
- 200 OK with only the authenticated user’s sessions.

Manual negative tests:
- Missing Authorization header: expect 401 from backend.
- Invalid/expired token: expect 401.
- Craft an attempt to insert a session with a different user_id (if route allowed such a body) should fail RLS with 42501 or be rejected by application logic.
- Cross-user access (use a different user’s token to GET a session by id that you do not own) should yield 404/forbidden (depending on implementation and RLS).

Automated tests (pytest suggestions):
- Use a fixture to create test users and obtain their JWTs (or mock sb.auth.get_user).
- Test:
  - create_session uses get_supabase_user_scoped and succeeds with correct user_id.
  - list_sessions returns only rows for that user.
  - get_session returns 404 when requesting another user’s session.
  - update_session and delete_session enforce ownership.
- Optional: Direct PostgREST checks using httpx with Authorization header to confirm RLS behavior on tables if you expose PostgREST endpoint (not typical in production).

## Rollout Plan
1) Staging:
   - Apply or verify the migration kb-backend/migrations/001_init.sql in a staging Supabase project.
   - Confirm extensions, tables, constraints, indexes, and policies exist and RLS is enabled.
   - Configure environment variables in staging backend:
     - SUPABASE_URL
     - SUPABASE_SERVICE_ROLE_KEY
     - SUPABASE_ANON_KEY
     - SUPABASE_JWT_SECRET (optional, recommended)
   - Validate manually using sample JWTs (signup/login flow) with curl tests above.
2) Deploy backend changes (if any):
   - Ensure the user-scoped Supabase client behavior is active (already present in src/api/deps.py).
   - Confirm all endpoints that hit RLS-protected tables use get_supabase_user_scoped.
3) Monitor:
   - Supabase logs and audit trails for RLS denials and auth issues.
   - Backend logs for 401/403/42501 errors.
4) Production:
   - Apply migrations and deploy backend.
   - Repeat smoke tests with real user accounts.

## Troubleshooting
Common errors:
- 42501 RLS violation:
  - Verify client.postgrest.auth(token) is called (src/api/deps.py).
  - Ensure Authorization header is present on API requests.
  - Confirm insert body sets user_id = current user id.
  - Check RLS enabled on table and policies match WITH CHECK/USING conditions.
- 401/403 auth errors:
  - Missing/invalid Authorization header.
  - JWT expired or audience/issuer mismatch.
  - SUPABASE_JWT_SECRET mismatch (for local decode) or incorrect algorithm.
- Incorrect alg/kid:
  - Ensure tokens are HS256 if using SUPABASE_JWT_SECRET to decode locally; otherwise rely on sb.auth.get_user.
- Missing env vars:
  - SUPABASE_URL and server key (service/anon) must be set for backend.
  - The frontend must not receive service role key.
- How to verify active policies and session role:
  - Use Supabase Dashboard SQL to list policies:
    select * from pg_policies where schemaname='public';
  - Confirm session role and current user in SQL:
    select current_user, current_setting('request.jwt.claim.sub', true);

## Appendix
- Reference files in this repo:
  - kb-backend/migrations/001_init.sql (tables, RLS policies)
  - kb-backend/Backend/src/api/deps.py (user-scoped Supabase client and JWT validation)
  - kb-backend/Backend/src/api/routers/sessions.py (CRUD using RLS-aware client)
- Supabase docs:
  - Row Level Security: https://supabase.com/docs/guides/database/postgres/row-level-security
  - Policies: https://supabase.com/docs/guides/database/postgres/policies
  - PostgREST auth.uid() behavior: https://postgrest.org/en/stable/auth.html
- Notes:
  - Keep secrets in environment variables (never in client code).
  - Prefer server-side service role usage for maintenance. For end-user DB access via backend, always forward the user token to PostgREST.
