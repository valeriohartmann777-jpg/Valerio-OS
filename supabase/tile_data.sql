-- Vitality Base: optional real saving across devices.
--
-- Run this ONCE in your own free Supabase project (SQL editor). Then add these
-- to your Vercel env vars (and .env.local for local dev):
--   NEXT_PUBLIC_SUPABASE_URL
--   NEXT_PUBLIC_SUPABASE_ANON_KEY
--
-- This is a single-user personal setup with no login, so the anon key is public
-- in the browser. Treat this data as not-secret, or add auth later.
-- Safe to run more than once.

create table if not exists public.tile_data (
  tile_id    text primary key,
  data       jsonb not null default '[]'::jsonb,
  updated_at timestamptz not null default now()
);

alter table public.tile_data enable row level security;

-- Personal project: let the anon key read and write your own dashboard data.
grant select, insert, update on public.tile_data to anon, authenticated;

drop policy if exists "tile_data anon select" on public.tile_data;
drop policy if exists "tile_data anon insert" on public.tile_data;
drop policy if exists "tile_data anon update" on public.tile_data;

create policy "tile_data anon select" on public.tile_data
  for select using (true);
create policy "tile_data anon insert" on public.tile_data
  for insert with check (true);
create policy "tile_data anon update" on public.tile_data
  for update using (true) with check (true);
