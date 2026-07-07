# Vitality Base Dashboard

Your own personal dashboard, forkable in a couple of minutes. It is the exact
Vitality home screen: an animated poster grid over a living backdrop. Every tile is
an empty **slot** that you fill by following a step-by-step build (on Patreon) or by
building your own.

**No backend. No login. No accounts.** Fork it, deploy it, done.

---

## Deploy in 2 minutes

1. **Use this template** (green button on GitHub) to create your own repo.
2. **Deploy to Vercel**: import the repo and click Deploy. There are **no environment
   variables** to set.

   [![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https://github.com/RowanThistlebrooke/vitality-base)

That is it. Your dashboard is live.

### Make it yours

Edit one line in [`content/site.ts`](content/site.ts) to put your name in the
greeting:

```ts
export const site = { name: 'Your Name' }
```

### Level up: real saving (optional)

By default your data saves in the browser, per device. To sync across your phone and
laptop, add your own free Supabase project:

1. Create a project at https://supabase.com
2. In the SQL editor, run [`supabase/tile_data.sql`](supabase/tile_data.sql)
3. Add two env vars (in Vercel, and `.env.local` for local dev):

```bash
NEXT_PUBLIC_SUPABASE_URL=your-project-url
NEXT_PUBLIC_SUPABASE_ANON_KEY=your-anon-key
```

Redeploy and your tiles save for real across devices. This is a single-user personal
setup with no login, so the anon key is public in the browser: treat the data as
not-secret, or add auth later.

## Run it locally

```bash
git clone <your-fork-url>
cd vitality-base
npm install
npm run dev
```

Then open http://localhost:3000. Requires Node 20+ (see `.nvmrc`).

---

## Filling the tiles

Click any tile and it opens a panel telling you how to build it. Each tile is a slot
that fills when a file exists at `public/tiles/<slot>.html`. Two ways to fill one:

- **Follow a build.** Each Patreon episode ships a slash command (e.g. `/logger`).
  Drop it into `.claude/commands/`, run it in Claude Code, and it writes the tile
  straight into the right slot. Commit, redeploy, and it appears.
- **Build your own.** Run [`/tile <slot>`](.claude/commands/tile.md) in Claude Code
  (or ask it to build a `<slot>` tile and save it to `public/tiles/<slot>.html`).

A tile is one self-contained HTML file. It saves its own data through the dashboard
bridge, `window.Vitality.save()` and `window.Vitality.load()`, which the dashboard
provides. Full contract: [`public/tiles/README.md`](public/tiles/README.md).

The slots: `train`, `fuel`, `vitals`, `vee`, `brand`, `peak`, `finance`.

---

## Tech

Next.js 14 (App Router), vanilla CSS, Three.js for the header gem, deployed on
Vercel. A tile's data lives in your browser, or in your own Supabase project if you
add one.
