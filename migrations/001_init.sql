-- 001_init.sql
-- Purpose: Initialize core tables and RLS policies for KnowledgeBot on Supabase
-- Tables:
--   - profiles
--   - sessions
--   - messages
--   - files
-- Notes:
--   - All tables have RLS enabled and safe policies so users can access only their own data.
--   - References use Supabase Auth users (auth.users).
--   - An optional trigger is provided (commented) to auto-create profiles on user signup.

-- Extensions required (typically enabled on Supabase projects)
-- gen_random_uuid() requires pgcrypto OR use the Supabase preferred extension.
create extension if not exists pgcrypto;

-- =========
-- profiles
-- =========
create table if not exists public.profiles (
  id uuid primary key, -- same as auth.users.id
  email text,
  full_name text,
  created_at timestamptz not null default now()
);

-- Ensure id matches auth.users
alter table public.profiles
  add constraint profiles_id_fk_auth_users
  foreign key (id)
  references auth.users (id)
  on delete cascade;

-- Indexes
create index if not exists idx_profiles_email on public.profiles (email);

-- Enable Row Level Security
alter table public.profiles enable row level security;

-- RLS policies for profiles (self-only)
drop policy if exists "Profiles: owners can select self" on public.profiles;
create policy "Profiles: owners can select self"
  on public.profiles
  for select
  using (auth.uid() = id);

drop policy if exists "Profiles: owners can insert self" on public.profiles;
create policy "Profiles: owners can insert self"
  on public.profiles
  for insert
  with check (auth.uid() = id);

drop policy if exists "Profiles: owners can update self" on public.profiles;
create policy "Profiles: owners can update self"
  on public.profiles
  for update
  using (auth.uid() = id)
  with check (auth.uid() = id);

drop policy if exists "Profiles: owners can delete self" on public.profiles;
create policy "Profiles: owners can delete self"
  on public.profiles
  for delete
  using (auth.uid() = id);

-- =========
-- sessions
-- =========
create table if not exists public.sessions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  title text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_sessions_user_id on public.sessions (user_id);
create index if not exists idx_sessions_created_at on public.sessions (created_at);

-- Trigger to auto-update updated_at
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- Ensure no leftover trigger definition exists before creating it
drop trigger if exists t_sessions_set_updated_at on public.sessions;

-- Create trigger with explicit EXECUTE FUNCTION syntax (PostgreSQL 13+)
create trigger t_sessions_set_updated_at
  before update on public.sessions
  for each row execute function public.set_updated_at();

-- Enable RLS
alter table public.sessions enable row level security;

-- RLS: only owners can access/modify their sessions
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

-- =========
-- messages
-- =========
create table if not exists public.messages (
  id uuid primary key default gen_random_uuid(),
  session_id uuid not null references public.sessions(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role text not null check (role in ('user', 'assistant', 'system')),
  content text not null,
  created_at timestamptz not null default now()
);

create index if not exists idx_messages_session_id on public.messages (session_id);
create index if not exists idx_messages_user_id on public.messages (user_id);
create index if not exists idx_messages_created_at on public.messages (created_at);

-- Enable RLS
alter table public.messages enable row level security;

-- RLS: users can only access messages belonging to sessions they own
-- Note: This uses a join via 'using' to compare auth.uid() to sessions.user_id
drop policy if exists "Messages: owners can select" on public.messages;
create policy "Messages: owners can select"
  on public.messages
  for select
  using (
    exists (
      select 1
      from public.sessions s
      where s.id = messages.session_id
        and s.user_id = auth.uid()
    )
  );

drop policy if exists "Messages: owners can insert" on public.messages;
create policy "Messages: owners can insert"
  on public.messages
  for insert
  with check (
    user_id = auth.uid()
    and exists (
      select 1 from public.sessions s
      where s.id = messages.session_id
        and s.user_id = auth.uid()
    )
  );

drop policy if exists "Messages: owners can update" on public.messages;
create policy "Messages: owners can update"
  on public.messages
  for update
  using (
    user_id = auth.uid()
    and exists (
      select 1 from public.sessions s
      where s.id = messages.session_id
        and s.user_id = auth.uid()
    )
  )
  with check (
    user_id = auth.uid()
    and exists (
      select 1 from public.sessions s
      where s.id = messages.session_id
        and s.user_id = auth.uid()
    )
  );

drop policy if exists "Messages: owners can delete" on public.messages;
create policy "Messages: owners can delete"
  on public.messages
  for delete
  using (
    user_id = auth.uid()
    and exists (
      select 1 from public.sessions s
      where s.id = messages.session_id
        and s.user_id = auth.uid()
    )
  );

-- =====
-- files
-- =====
create table if not exists public.files (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  session_id uuid null references public.sessions(id) on delete set null,
  name text not null,
  storage_path text not null,
  mime_type text,
  size bigint,
  created_at timestamptz not null default now()
);

create index if not exists idx_files_user_id on public.files (user_id);
create index if not exists idx_files_session_id on public.files (session_id);
create index if not exists idx_files_created_at on public.files (created_at);

-- Enable RLS
alter table public.files enable row level security;

-- RLS: only owners can access their files
drop policy if exists "Files: owners can select" on public.files;
create policy "Files: owners can select"
  on public.files
  for select
  using (auth.uid() = user_id);

drop policy if exists "Files: owners can insert" on public.files;
create policy "Files: owners can insert"
  on public.files
  for insert
  with check (auth.uid() = user_id);

drop policy if exists "Files: owners can update" on public.files;
create policy "Files: owners can update"
  on public.files
  for update
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

drop policy if exists "Files: owners can delete" on public.files;
create policy "Files: owners can delete"
  on public.files
  for delete
  using (auth.uid() = user_id);

-- =========================================
-- Optional: Auto-insert profile on new user
-- =========================================
-- Supabase emits 'auth.users' changes via a special schema 'auth'.
-- The recommended approach today is to create the profile in your backend after signup.
-- If you'd like to auto-insert with a database trigger, you can uncomment below.

-- create or replace function public.handle_new_user()
-- returns trigger
-- language plpgsql
-- security definer
-- set search_path = public
-- as $$
-- begin
--   insert into public.profiles (id, email, full_name)
--   values (new.id, new.email, coalesce(new.raw_user_meta_data->>'full_name', ''))
--   on conflict (id) do update
--     set email = excluded.email,
--         full_name = coalesce(excluded.full_name, public.profiles.full_name);
--   return new;
-- end;
-- $$;

-- drop trigger if exists on_auth_user_created on auth.users;
-- create trigger on_auth_user_created
--   after insert on auth.users
--   for each row execute function public.handle_new_user();

-- =========
-- Security
-- =========
-- By default, Supabase policies already restrict access via anon/service roles.
-- No public grants are added here beyond policies above.

-- EOF
