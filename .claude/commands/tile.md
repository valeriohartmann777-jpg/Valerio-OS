---
description: Build a self-contained Vitality dashboard tile and drop it into its slot.
---

You are building ONE dashboard tile for a Vitality Base fork.

The argument is the slot name. It must be exactly one of:
`train, fuel, vitals, vee, brand, peak, finance`

If no slot is given, ask which one, then continue.

Build the tile as a single self-contained HTML file and write it to:
`public/tiles/<slot>.html`

Rules (the Sealed Tile Contract):

1. One file. All CSS and JS inline. No external requests, no imports, no CDN links, no fonts
   over the network. The tile runs in a sandboxed iframe with no network, so anything not inline
   will not load.
2. Match the look: pure black background, mint accent `#6EE7B7`, Inter or system font, minimal
   and premium. No emojis in the UI.
3. Save data through the host bridge, never localStorage (localStorage is blocked inside the
   sealed tile). The dashboard provides `window.Vitality` for you:
   - `await window.Vitality.save(data)` to persist (data is JSON; an array of records is the
     natural shape).
   - `const data = await window.Vitality.load()` to read it back (returns `[]` when empty).
   Do not define `window.Vitality` yourself. The dashboard injects it at mount.
4. On load, call `window.Vitality.load()` first and render whatever comes back, so the tile
   restores its state every time it opens.

After writing the file, tell the user to commit and reload the dashboard so the `<slot>` tile
fills.
