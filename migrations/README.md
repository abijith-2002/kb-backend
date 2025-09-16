# Supabase Migrations for KnowledgeBot

This folder contains SQL migrations to initialize the PostgreSQL schema and Row Level Security (RLS) policies for KnowledgeBot.

## Files

- `001_init.sql`: Creates tables and policies:
  - `profiles` (1:1 with `auth.users`)
  - `sessions` (per-user chat sessions)
  - `messages` (chat messages within a session)
  - `files` (uploaded files, optionally linked to a session)
  - RLS policies that allow each user to access and modify only their own data.
  - Helper trigger to maintain `updated_at` on `sessions`.
  - Optional (commented) trigger function to auto-create a `profiles` row on user signup. We recommend handling this in your backend after signup, but the trigger is provided for convenience.

## Applying these migrations

You can apply this SQL in your Supabase project in one of two ways:

1) Using Supabase Dashboard:
   - Open your project -> SQL Editor.
   - Copy paste the contents of `001_init.sql` into a new query and run it.
   - Confirm tables and policies appear under the `public` schema.

2) Using Supabase CLI:
   - Ensure you have `supabase` CLI installed and authenticated.
   - Place these files under your project’s `supabase/migrations` folder or run:
     ```bash
     supabase db push --file path/to/kb-backend/migrations/001_init.sql
     ```
   - Alternatively, from the project root if your migrations are already under `supabase/migrations`, simply:
     ```bash
     supabase db push
     ```

## Notes

- The schema uses `gen_random_uuid()` from `pgcrypto`. The migration enables the extension if needed.
- All tables have RLS enabled with safe policies:
  - `profiles`: users can manage only their own profile (matching `auth.uid()`).
  - `sessions`: access is limited by `user_id`.
  - `messages`: access is limited to sessions owned by the user.
  - `files`: access is limited by `user_id`. If a file is linked to a session, that does not change its ownership.
- If you decide to enable the signup trigger for `profiles`, uncomment the relevant section near the end of `001_init.sql`, then run the migration again. Otherwise, ensure your backend upserts into `public.profiles` on user creation with the `auth.uid()`.

## Environment/Usage

- No environment variables are required for these migrations.
- Ensure your application uses Supabase Auth; policies rely on `auth.uid()` being populated by the JWT.

## Verification Checklist

After applying the migration:
- [ ] `public.profiles`, `public.sessions`, `public.messages`, and `public.files` exist.
- [ ] RLS is enabled on all those tables.
- [ ] Selecting another user's rows fails as expected.
- [ ] Inserting a row for another `user_id` fails as expected.
- [ ] `sessions.updated_at` updates on row updates.
