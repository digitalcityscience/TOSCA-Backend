Based on the design we just built together, here is the official UI/UX specification for TOSCA.

***

# TOSCA — UI/UX Design Specification

**Version:** 0.1 — Phase 1
**Stack:** Django + DRF + Geo Engine Adapters
**Last updated:** March 2026

***

## 1. Design Philosophy

TOSCA is a **developer-facing backend management system**, not a public product. Every UI decision prioritizes:

- **Clarity over decoration** — no gradients, no shadows, no icons for their own sake
- **Information density** — small font sizes, tight spacing, more content per screen
- **Dark by default** — reduces eye strain during long backend work sessions
- **Predictability** — same patterns repeated across all pages, no surprises

The closest reference points are Linear, Vercel Dashboard, and Railway — not generic admin panels.

***

## 2. Design Tokens

All values live in `:root` CSS custom properties. Nothing is hardcoded anywhere else.

### Colors

```
--bg:          #0d0d0d   Page background
--surface:     #141414   Sidebar, topbar, cards
--surface-2:   #1c1c1c   Hover states, inputs, inner panels
--border:      #262626   Default borders
--border-2:    #333333   Stronger borders, focused elements

--text:        #e8e8e8   Primary text
--text-muted:  #666666   Secondary text, labels, placeholders
--text-dim:    #3a3a3a   Section labels, disabled hints

--accent:      #4f86f7   Interactive elements, active states, CTA
--accent-dim:  rgba(79,134,247,.10)   Active nav background

--green:       #3ecf8e   Success, connected, synced
--green-dim:   rgba(62,207,142,.10)
--orange:      #f5a623   Warning, drift, pending
--orange-dim:  rgba(245,166,35,.10)
--red:         #e54d4d   Error, disconnected, failed
--red-dim:     rgba(229,77,77,.10)
```

### Typography

```
Font:        "Inter", -apple-system, BlinkMacSystemFont, sans-serif
Code/Mono:   "JetBrains Mono", "Fira Code", monospace

Body:        13px / 1.5   Regular    Default content
Small:       12px / 1.6   Regular    Descriptions, meta
Label:       10px / —     600        Section headers (uppercase + letter-spacing)
Page title:  20–22px      700        H1 per page
Card title:  14px         600        App card names
Mono:        12px         —          URLs, IDs, EPSG codes, API keys
```

### Spacing

```
4px   — micro gap (icon + label)
6px   — inside small components
8px   — nav item padding, small gaps
10px  — card grid gap
12px  — sidebar padding, footer
20px  — card inner padding
24px  — section spacing
32px  — main content padding
36px  — main content side padding
```

### Border Radius

```
--r:    6px    Buttons, nav items, badges
--r-lg: 10px   Cards, panels
5px           Logo mark
50%           Avatars, status dots
20px          Pills, badges
```

***

## 3. Layout Architecture

TOSCA uses a fixed **sidebar + topbar + main** shell defined with CSS Grid.

```
┌──────────────┬─────────────────────────────────┐
│              │  TOPBAR  [TOSCA]     [user] [→]  │
│   SIDEBAR    ├─────────────────────────────────┤
│              │                                 │
│  Applications│  MAIN                           │
│  ──────────  │  (page content here)            │
│  Home        │                                 │
│  Geo Console │                                 │
│  Particip.   │                                 │
│  Geo Eng API │                                 │
│              │                                 │
└──────────────┴─────────────────────────────────┘
```

| Zone | Element | Size | Behavior |
|---|---|---|---|
| Sidebar | `<aside>` | 200px wide, full height | Fixed, never scrolls |
| Topbar | `<header>` | 48px tall, full width | Fixed, always visible |
| Main | `<main>` | flex: 1, fills remaining | Scrollable |

Grid definition:
```css
grid-template-areas: "sidebar topbar" "sidebar main";
```

***

## 4. Sidebar

- Background: `--surface`, right border: `--border`
- Section label: 10px, uppercase, `--text-dim` — used to group nav items ("Applications")
- Nav items: 13px, `--text-muted` at rest → `--text` on hover with `--surface-2` background
- Active item: `--accent-dim` background + `--accent` text — no left border stripe needed
- Bottom footer: user avatar (initials) + username + role + logout arrow

### Nav Structure (Phase 1)

```
Applications
─────────────
Home
─────────────
Geo Console
Participation
Geo Engine API
```

Each future app gets its own entry here. Sub-navigation (Workspaces, Stores, Layers) lives **inside** the Geo Console pages, not in this global sidebar.

***

## 5. Topbar

- Background: `--surface`, bottom border: `--border`
- Left: Logo mark (22×22px, accent blue square) + "TOSCA" wordmark
- Right: Auth area — two states:

**Logged out:**
```
[ Login ]   ← ghost button, border: --border-2
```

**Logged in:**
```
[ AD  admin ]   [ Logout ]
  ↑ user-pill     ↑ subtle text link
```

The user-pill is a rounded container with a small avatar (initials, accent-colored border) and the username. Logout is a plain text link — no button styling, color `--text-dim`.

***

## 6. Home Page

The home page is the **app launcher**. It has two parts:

### Welcome block
```
Welcome to TOSCA Backend          ← 22px, 700
Geospatial data management...     ← 13px, --text-muted
```

### App Cards Grid
- `grid-template-columns: repeat(auto-fill, minmax(240px, 1fr))`
- Gap: 10px
- Each card: `--surface` background, `--border` border, `--r-lg` radius

**Active app card** (`<a>` tag, fully clickable):
- Hover: `border-color` → `--border-2`, `background` → `--surface-2`
- Shows: App name + green "Active" badge + description + meta info + version badge

**Inactive app card** (`<div>`, not clickable):
- `opacity: 0.35`, `pointer-events: none`
- Shows: App name + neutral "Coming soon" badge + description

***

## 7. Component States

Every interactive element must handle all states before going to production:

| State | Rule |
|---|---|
| Default | Resting appearance |
| Hover | `background: --surface-2`, `color: --text`, transition 100–140ms |
| Active/Selected | `background: --accent-dim`, `color: --accent` |
| Disabled | `opacity: 0.35`, `pointer-events: none` — never just grayed color |
| Focus | `outline: 2px solid --accent`, `outline-offset: 2px` — keyboard accessible |
| Loading | Button text changes to "Loading…", add spinner, disable clicks |
| Error | Red border on input + helper text below — never alert boxes |
| Empty | Explicit message + action CTA — never a blank area |

***

## 8. Badges

Used for status, type labels, and versions. Always include text — never color alone.

```
badge-green    #3ecf8e on green-dim   → Active, Connected, Synced
badge-orange   #f5a623 on orange-dim  → Warning, Drift, Pending
badge-red      #e54d4d on red-dim     → Error, Failed, Offline
badge-blue     #4f86f7 on accent-dim  → Info, Selected
badge-neutral  --text-muted, border   → Coming soon, version numbers
```

***

## 9. What Belongs Where

This is the most important structural rule:

| Content type | Lives in |
|---|---|
| Global navigation (apps) | `base.html` sidebar |
| Auth (login/logout/user) | `base.html` topbar |
| App launcher | `home.html` |
| App-level sub-navigation | Inside that app's own `base_appname.html` |
| Page-level actions (Add, Filter) | Inside each page template |
| Design tokens | `base.html` `:root` — nowhere else |

***

## 10. What Not to Do

- ❌ Hardcoded colors or pixel values outside `:root`
- ❌ SVG icon sets for navigation — use text labels; icons only when they add meaning
- ❌ Full-page modals for simple confirmations — use inline confirmation
- ❌ `window.alert()` or `window.confirm()` — never
- ❌ Placeholder text as a substitute for a `<label>`
- ❌ Color as the only indicator of status — always pair with text
- ❌ Designing only the happy path — empty states and error states are required
- ❌ Sub-navigation in the global sidebar — that belongs inside each app