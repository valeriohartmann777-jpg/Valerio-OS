-- ====================================================================
--  THE LIVING LOGGER, BACKEND. Additive, one episode at a time.
--  Run this once in your Supabase SQL editor to sync the logger across
--  your devices. After it, a set you log on your laptop shows up on your
--  phone. It is safe to re-run: every statement is guarded (if not exists).
--
--  Each region matches a feature region in logger.html:
--    chart    (Episode 1)  client-only, no table of its own
--    logger   (Episode 2)  establishes the one workout_log table + RLS
--    library  (Episode 3)  one additive column so lifts can link to the catalog
--  A later episode just appends the next region below, never a new store.
-- ====================================================================

-- @episode-start:chart
-- Episode 1 (progressive overload) is CLIENT-ONLY. The chart draws from the
-- lift's own logged sessions, which live in the shared workout_log row created
-- by the logger region below (the sets column carries the per-session history).
-- There is no separate chart table: the chart is a view of the same data.
-- @episode-end:chart

-- @episode-start:logger
-- Episode 2 (the workout logger) establishes the one table the whole app grows on.
create table if not exists workout_log (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null default auth.uid() references auth.users(id) on delete cascade,
  lift        text not null,
  target_sets int  not null default 3,
  target_reps int  not null default 8,
  weight      numeric not null default 0,        -- last / target weight, in kg
  sets        jsonb   not null default '[]',     -- [{ weight, reps, done, failed }]
  position    int     not null default 0,        -- your reorder order
  hidden      boolean not null default false,    -- soft delete: removing hides, never destroys
  updated_at  timestamptz not null default now()
);

-- Multi-user safe. Each person only ever reads and writes their own rows.
alter table workout_log enable row level security;

create policy "read own lifts"   on workout_log for select using (auth.uid() = user_id);
create policy "insert own lifts" on workout_log for insert with check (auth.uid() = user_id);
create policy "update own lifts" on workout_log for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "delete own lifts" on workout_log for delete using (auth.uid() = user_id);
-- @episode-end:logger

-- @episode-start:library
-- Episode 3 (the exercise library) adds ONE column, additively: when a lift comes
-- from the catalog it stores its library key, so its official info card (muscles,
-- tier, steps, cues, photos) and its saved identity follow it. Custom-typed lifts
-- leave this null. No new table, no data migration: the library rides the same row.
alter table workout_log
  add column if not exists lib_key text;   -- catalog key, e.g. 'bench_bb'; null for custom lifts
-- @episode-end:library

-- @episode-start:rest-timer
-- Episode 4 (the rest coach) remembers each lift's preferred rest, so the timer arms
-- itself to the right number instead of a flat default. Additive: one column on the
-- same one table, defaulted to match the app. Nothing already shipped changes.
alter table workout_log
  add column if not exists rest_sec int not null default 150;   -- preferred rest, in seconds
-- @episode-end:rest-timer

-- @episode-start:session
-- Episode 4 (the customizable split) turns the one flat list into a real split:
-- named days, each holding its own lifts. Two additive changes, no data migration.
--
-- (a) Tag every lift row with the day it belongs to, so one workout_log table can hold
--     a whole multi-day split. Defaults to 'Day 1', so anything already saved just
--     becomes a single-day split, exactly like the client-side v3 -> v4 migration.
alter table workout_log
  add column if not exists day_name text not null default 'Day 1';   -- which split day this lift lives on

-- (b) One small table for the split itself: the ordered list of days (names + order),
--     which day is active, and the unit preference. jsonb keeps it as simple as the
--     client's DATA object, one row per user.
create table if not exists training_split (
  user_id    uuid primary key default auth.uid() references auth.users(id) on delete cascade,
  days       jsonb not null default '[]',     -- [{ id, name }] in training order (the lifts live in workout_log, keyed by day_name)
  active_day text,                             -- the day shown first when the logger opens
  unit       text not null default 'kg',       -- display unit; weights are always stored in kg
  updated_at timestamptz not null default now()
);

-- Multi-user safe, same own-row pattern as the logger table: you only ever see yours.
alter table training_split enable row level security;

create policy "read own split"   on training_split for select using (auth.uid() = user_id);
create policy "insert own split" on training_split for insert with check (auth.uid() = user_id);
create policy "update own split" on training_split for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "delete own split" on training_split for delete using (auth.uid() = user_id);

-- Note: Vitality's hosted split is richer on purpose. The real product carries
-- periodization (heavy vs volume weeks), day TYPES, and per-lift tiers in
-- training_settings.rotation_days. This demo keeps just names + lifts so the one file
-- stays simple; the shape here is the honest, stripped-down core of that.
-- @episode-end:session

-- @episode-start:stack
-- Episode 2 (the supplement stack). One row per person: the whole
-- stack lives in jsonb, same own-row pattern as everything above. Additive,
-- safe to re-run.
create table if not exists supplement_stack (
  user_id      uuid primary key default auth.uid() references auth.users(id) on delete cascade,
  items        jsonb not null default '[]',   -- [{ id | custom name, dose, window }]
  taken_by_day jsonb not null default '{}',   -- { "YYYY-MM-DD": ["name", ...] }
  off_blocks   jsonb not null default '[]',   -- time blocks the user turned off
  updated_at   timestamptz not null default now()
);

alter table supplement_stack enable row level security;

do $$ begin
  if not exists (select 1 from pg_policies where tablename = 'supplement_stack' and policyname = 'read own stack') then
    create policy "read own stack"   on supplement_stack for select using (auth.uid() = user_id);
    create policy "insert own stack" on supplement_stack for insert with check (auth.uid() = user_id);
    create policy "update own stack" on supplement_stack for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
    create policy "delete own stack" on supplement_stack for delete using (auth.uid() = user_id);
  end if;
end $$;
-- @episode-end:stack
