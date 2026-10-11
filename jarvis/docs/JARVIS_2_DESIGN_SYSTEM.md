# JARVIS 2.0 — design system

Direction (brief §2): 2018 game-lobby energy — big uppercase headings, chunky cards with a
heavy bottom edge, one bright yellow call to action, a colour per area — on a calm navy
system UI that stays readable for hours. No third-party game assets: robots, icons and the
HQ are drawn for JARVIS.

Source of truth: `apps/desktop/src/styles/index.css` (tokens), `components/ui/icons.tsx`,
`components/ui/primitives.tsx`, `components/ui/shell.tsx`, `components/ultron/hq/*`.

## 1. Tokens

| Role | Token / Tailwind | Value |
|---|---|---|
| App background | `--color-canvas` / `bg-canvas` | `#071020` (+ radial area glows) |
| Deeper background | `--color-canvas-2` | `#0a1529` |
| Card | `--color-surface` / `bg-surface` | `#15243d` |
| Raised card, inputs | `--color-surface-2` | `#1c3050` |
| Region border | `--color-hairline` | `rgb(141 170 214 / .16)` |
| Card border | `--color-hairline-strong` | `#354c69` |
| Control border | `--color-control` | `#5b7598` |
| Text primary / secondary / tertiary | `fg` / `fg-muted` / `fg-faint` | `#f8fbff` / `#becfe7` / `#93a8c6` |
| Area: Home · Training · QuantLab · ULTRON · Settings | `home` `training` `quant` `ultron` `settings` | `#54cfff` `#b29aff` `#42d7b6` `#ffb45d` `#bac9dd` |
| Call to action | `cta` / `cta-ink` | `#ffe16b` on `#0b1730` |
| Status | `success` `warning` `danger` | `#4cdfac` `#ffc46b` `#ff747d` |

`ql-*` tokens of the QuantLab components point at the same palette, so older QuantLab
screens follow the new look without a rewrite.

Every page sets `--area` to its colour; focus rings, active sub-navigation and section links
use it, so the user always sees which area they are in.

### Contrast (WCAG 2.x, computed from the tokens)

| Text | on canvas | on surface | on surface-2 |
|---|---|---|---|
| fg | 18.3 | 15.0 | 12.7 |
| fg-muted | 12.0 | 9.8 | 8.4 |
| fg-faint | 7.8 | 6.4 | 5.5 |
| area colours (lowest: training) | ≥ 8.1 | ≥ 6.6 | ≥ 5.6 |
| danger (lowest status) | 7.3 | 6.0 | 5.1 |
| cta-ink on cta | 13.8 | | |

Control borders (`#5b7598`) reach 3.3 : 1 against cards. **High contrast** (Settings →
Interface) raises secondary text to `#dbe6f5`, tertiary to `#c0d0e6` and all borders.

## 2. Type and size

- Inter (UI), JetBrains Mono (numbers, ids, code).
- Page title 44–52 px black uppercase with a full stop ("TRAINING HUB."); eyebrow 12 px
  tracking 0.2 em in the area colour; panel titles 14–15 px black uppercase.
- Body 14–16 px; labels and meta ≥ 12 px (`text-2xs` = 12 px). Nothing smaller.
- Controls 36 px (`md`) or 44 px (`lg`, primary actions such as *Run backtest*); tabs 64 px high.

## 3. Components

| Component | File | Notes |
|---|---|---|
| `Icon` | `ui/icons.tsx` | one map of 24×24 stroke icons drawn for JARVIS; decorative unless `label` is given |
| `Button` | `ui/primitives.tsx` | `primary` (yellow CTA with a 3 px bottom edge), `ghost`, `danger`, `warning`; `size md/lg`; **`reason`**: a disabled button always says why (tooltip + accessible description) |
| `PageHeader` | `ui/shell.tsx` | eyebrow, uppercase title, subtitle, optional actions |
| `Panel` | `ui/shell.tsx` | card with title and `aside` (status pill, link) |
| `SubNav` | `ui/shell.tsx` | section tabs inside a page (`aria-current`, testid `subnav-<id>`) |
| `StatusPill` | `ui/shell.tsx` | `success · info · warning · danger · muted` — always text + colour |
| `WorkspaceModeBadge` | `ui/shell.tsx` | *Solo workspace* (lock, mint) / *Team workspace* (amber) |
| `EmptyState` · `LoadingState` · `ErrorState` | `ui/shell.tsx` | every async view uses one of them instead of a blank area; errors say what to do |
| `Tile` | `ui/shell.tsx` | Home area tiles: icon, label, real status |
| `SafeErrorBoundary` | `ui/shell.tsx` | a broken page shows its error and a retry; the shell keeps working |
| `RobotFigure` | `ultron/hq/Robot.tsx` | nine original SVG robots: shared body, per-robot colour, antenna tip and chest emblem; eyes closed when docked; badges for approval / blocked / failed / done |
| `World` · `RoomTile` · `Crew` · `Inspector` | `ultron/hq/` | HQ drawing, accessible crew list, robot details |

## 4. Motion

- Only state carries motion: a working robot bobs (`hq-bob`), a docked one shows a slow
  "z" (`hq-z`); route changes fade. No ambient loops.
- **Reduced motion** (Settings → Interface, or the OS setting) turns all of it off
  (`[data-motion=reduced]`, Framer Motion `MotionConfig`).

## 5. Accessibility rules

- Five tabs in a `nav` labelled *Main*, `aria-current="page"`, arrow keys / Home / End.
- Visible 2 px focus ring in the area colour on every interactive element.
- Status is never colour alone: pills carry text, robots carry a state label (also in the
  crew list and their accessible name), feedback icons have labels.
- Disabled controls explain themselves (`reason`).
- The HQ drawing has an equivalent list view (crew bar; *Simple list* replaces the world).
- Layout works from the window minimum (1120 × 720) up; checked in the E2E run at
  1366 × 768 and 1120 × 720 (tabs, composer, *Run backtest*, HQ reachable; no sideways scroll).

## 6. Interface preferences

`localStorage["jarvis.interface"]` → attributes on `<html>`: `data-motion="reduced"`,
`data-contrast="high"`; `simpleHq` switches the HQ to the list. Per-device, not secret.
