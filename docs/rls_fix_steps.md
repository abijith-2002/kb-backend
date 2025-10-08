# Supabase RLS Fix – Step-by-Step Guide (POST /sessions)

## 1) Environment Setup
1. Set required backend environment variables (do not expose secrets to the frontend):
   - SUPABASE_URL
   - SUPABASE_SERVICE_ROLE_KEY (server only) or SUPABASE_ANON_KEY (fallback)
   - SUPABASE_JWT_SECRET (optional; enables local JWT decode)
   - CORS_ORIGINS (optional)
2. Ensure frontend uses only SUPABASE_URL and SUPABASE_ANON_KEY and sends Authorization: Bearer <access_token> to the backend for protected endpoints.
3. Confirm Supabase project has pgcrypto enabled (for gen_random_uuid()):
   - In SQL editor: create extension if not exists pgcrypto;

## 2) Backend Client Configuration (User-Scoped JWT Forwarding)
Critical requirement: PostgREST must receive the end-user JWT so that auth.uid() evaluates correctly inside RLS.

In FastAPI (implemented in src/api/deps.py):
- Extract the Bearer token from the request.
- Forward the user token to PostgREST (RLS identity):
  - client.postgrest.auth(token)
- Optionally ensure Authorization header is set on the underlying PostgREST client for SDK variations.
- Note: The Python SDK does not support client.auth.set_auth(token) on Sync clients; rely on postgrest.auth().

Function reference:
- Backend/src/api/deps.py: get_supabase_user_scoped(request)
- All sessions routes use this user-scoped client (see Backend/src/api/routers/sessions.py).

## 3) Required RLS Policies for sessions
Ensure RLS is enabled and owner-only policies exist on public.sessions:

```sql
alter table public.sessions enable row level security;

drop policy if exists "Sessions: owners can select" on public.sessions;
create policy "Sessions: owners can select"
  on public.sessions
  for select
  using (auth.uid() = user_id);

drop policy if exists "Sessions: owners can insert" on public.sessions;
create policy "Sessions: owners can insert"
  on public.sessions
  for insert
  with check (auth.uid() = user_id);

drop policy if exists "Sessions: owners can update" on public.sessions;
create policy "Sessions: owners can update"
  on public.sessions
  for update
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

drop policy if exists "Sessions: owners can delete" on public.sessions;
create policy "Sessions: owners can delete"
  on public.sessions
  for delete
  using (auth.uid() = user_id);
```

Schema prerequisites (if missing):
```sql
create extension if not exists pgcrypto;

create table if not exists public.sessions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  title text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_sessions_user_id on public.sessions (user_id);
create index if not exists idx_sessions_created_at on public.sessions (created_at);

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists t_sessions_set_updated_at on public.sessions;
create trigger t_sessions_set_updated_at
  before update on public.sessions
  for each row execute function public.set_updated_at();
```

## 4) Apply Migration Commands
Option A – Supabase Dashboard:
- Open SQL Editor, paste the SQL above (policies and any missing schema) and run.

Option B – CLI (if you maintain migrations locally):
- Place/verify SQL in kb-backend/migrations/001_init.sql (already present).
- Run:
  - supabase db push --file kb-backend/migrations/001_init.sql
  or copy the relevant policy blocks into a new migration and push.

## 5) Verify with curl and Logs
1. Obtain a valid JWT (via the frontend login or backend /auth/login):
```bash
BACKEND_URL="http://localhost:3002"
TOKEN="<your_supabase_access_token>"
```

2. Create a session (should succeed):
```bash
curl -s -X POST "$BACKEND_URL/sessions" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title": "My first session"}'
```
Expected: 201 with JSON containing user_id equal to the token’s user id.

3. List sessions (should show only your sessions):
```bash
curl -s -X GET "$BACKEND_URL/sessions" \
  -H "Authorization: Bearer $TOKEN"
```

4. Server/DB logs:
- Backend logs: ensure no 401/403/42501 errors during requests.
- Supabase logs/audit: confirm RLS policy hits and no denials for valid requests.

## 6) Troubleshooting
- Error 42501 “new row violates row-level security policy”:
  - Confirm Authorization header is present on the POST /sessions request.
  - Ensure backend uses user-scoped client (client.postgrest.auth(token)).
  - Verify insert payload sets user_id = current user’s id (see sessions.py: create_session).
  - Check RLS is enabled and the policies above exist.
- 401/403:
  - Token missing/expired/invalid.
  - SUPABASE_JWT_SECRET mismatch (for local decode); fallback to sb.auth.get_user.
- Still failing:
  - Print/log the user id resolved by get_current_user; ensure it matches auth.uid() expectation.
  - In SQL editor:
    - select current_user, current_setting('request.jwt.claim.sub', true);
    - select * from pg_policies where schemaname='public' and tablename='sessions';
- Environment:
  - Backend must have SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (or ANON) configured.
  - Never expose SERVICE_ROLE_KEY to the frontend.

## 7) Success Criteria
- Authenticated users can POST /sessions and receive 201 with their session.
- GET /sessions returns only their sessions.
- Cross-user access is denied by RLS.
- Backend logs show no 42501 for valid user-owned operations.

## References
- Repo SQL and code:
  - kb-backend/migrations/001_init.sql
  - kb-backend/Backend/src/api/deps.py
  - kb-backend/Backend/src/api/routers/sessions.py
- Supabase docs:
  - RLS: https://supabase.com/docs/guides/database/postgres/row-level-security
  - Policies: https://supabase.com/docs/guides/database/postgres/policies
  - PostgREST auth: https://postgrest.org/en/stable/auth.html
