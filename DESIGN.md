---
name: GG Dashboard
description: Fast, dense operator console with system light/dark and a muted slate-blue accent.
colors:
  light-bg: "#f6f7f9"
  light-surface: "#eef0f4"
  light-surface-raised: "#ffffff"
  light-ink: "#1a2230"
  light-ink-muted: "#4a5568"
  light-border: "#d8dde6"
  light-accent: "#4a6fa5"
  light-accent-hover: "#3d5f8f"
  light-danger-bg: "#fce8e8"
  light-danger-ink: "#9b2c2c"
  dark-bg: "#12151c"
  dark-surface: "#1a1f2a"
  dark-surface-raised: "#222836"
  dark-ink: "#e8ecf4"
  dark-ink-muted: "#9aa3b5"
  dark-border: "#2e3648"
  dark-accent: "#7a9fd4"
  dark-accent-hover: "#8eb0e0"
  dark-danger-bg: "#3a1f24"
  dark-danger-ink: "#f0a8a8"
typography:
  display:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-0.02em"
  title:
    fontFamily: "{typography.display.fontFamily}"
    fontSize: "1.125rem"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-0.01em"
  body:
    fontFamily: "{typography.display.fontFamily}"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  label:
    fontFamily: "{typography.display.fontFamily}"
    fontSize: "0.8125rem"
    fontWeight: 500
    lineHeight: 1.4
    letterSpacing: "normal"
rounded:
  sm: "6px"
  md: "8px"
  lg: "12px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "32px"
components:
  button-primary:
    backgroundColor: "{colors.dark-accent}"
    textColor: "{colors.dark-bg}"
    rounded: "{rounded.md}"
    padding: "10px 16px"
  button-primary-hover:
    backgroundColor: "{colors.dark-accent-hover}"
    textColor: "{colors.dark-bg}"
    rounded: "{rounded.md}"
    padding: "10px 16px"
  input-field:
    backgroundColor: "{colors.dark-surface-raised}"
    textColor: "{colors.dark-ink}"
    rounded: "{rounded.md}"
    padding: "10px 16px"
  nav-link-active:
    textColor: "{colors.dark-accent}"
---

## Overview

GG Dashboard is an operations console for club bot configuration: clubs, payment methods, flows, and staff settings. Visual tone is **fast and dense**: tight spacing, clear type hierarchy, minimal chrome. Color is **restrained**: cool neutrals carry most surfaces; slate-blue accent marks primary actions and active navigation only.

**Theme behavior:** Respect `prefers-color-scheme` by default (system). Optional manual override (light / dark / system) may persist in `localStorage` on `html[data-theme]`. Implementation uses semantic CSS variables (`--bg`, `--surface`, `--ink`, `--accent`, etc.) swapped per color scheme, not duplicate Tailwind utility strings per mode.

**Motion:** Subtle transitions on hover/focus only. Under `prefers-reduced-motion: reduce`, transitions collapse to instant or opacity-only crossfades.

Canonical OKLCH references (implement in `dashboard/src/index.css`):

| Token | Light | Dark |
|-------|-------|------|
| bg | `oklch(0.98 0.006 250)` | `oklch(0.16 0.02 250)` |
| surface | `oklch(0.96 0.008 250)` | `oklch(0.20 0.02 250)` |
| surface-raised | `oklch(1 0 0)` | `oklch(0.24 0.02 250)` |
| ink | `oklch(0.25 0.03 250)` | `oklch(0.93 0.01 250)` |
| ink-muted | `oklch(0.45 0.02 250)` | `oklch(0.65 0.02 250)` |
| border | `oklch(0.88 0.01 250)` | `oklch(0.32 0.02 250)` |
| accent | `oklch(0.52 0.08 250)` | `oklch(0.72 0.08 250)` |
| accent-hover | `oklch(0.46 0.09 250)` | `oklch(0.78 0.08 250)` |

## Colors

**Strategy:** Restrained tinted neutrals (hue ~250, low chroma) plus one slate-blue accent. No indigo-violet SaaS default, no warm cream body backgrounds, no gradient fills.

**Light mode:** Off-white cool background, white or near-white raised panels, ink text ≥4.5:1 on bg. Muted labels use `ink-muted`, not gray-500 on tinted panels.

**Dark mode:** Deep blue-gray base (not pure `#0a0a0a`), raised surfaces one step lighter for depth without nested card stacks. Accent is lighter in dark mode for the same perceived weight.

**Semantic colors:** Danger uses tinted backgrounds (`light-danger-bg` / `dark-danger-bg`) with readable ink; success/warning only when a state truly needs them (avoid rainbow status chips).

**Accent usage cap:** ≤10% of visible pixels on a typical screen (primary buttons, active nav, focus rings, key links). Tables, forms, and metadata stay neutral.

## Typography

Single sans stack (system UI). Hierarchy through **weight and size**, not extra font families.

| Role | Size | Weight | Use |
|------|------|--------|-----|
| Display | 1.5rem | 700 | Page titles (Clubs, Settings) |
| Title | 1.125rem | 600 | Section headers, panel titles |
| Body | 0.875rem | 400 | Default copy, table cells |
| Label | 0.8125rem | 500 | Form labels, column headers |

Body line length stays within the `max-w-6xl` content shell (~65–75ch in prose blocks). Use `text-wrap: balance` on page titles where they wrap.

## Elevation

Prefer **tonal layering** (bg → surface → surface-raised) over heavy shadows. Light mode: optional `0 1px 2px` shadow on modals only. Dark mode: borders (`border` token) define edges; shadows sparingly on floating elements.

No glassmorphism, no gradient borders, no left accent stripes on alerts.

## Components

**App shell:** Top nav on `surface`, 1px `border` bottom. Logo/title in `ink`; nav links `ink-muted` with `accent` for active route. Logout is a quiet secondary control (surface-raised + border), not a second primary button.

**Buttons:** One primary style (`accent` fill, `ink` on light primary text uses white or dark-bg depending on contrast check). Secondary = bordered ghost on `surface-raised`. Destructive = danger tokens, verb + object labels ("Delete club").

**Forms:** Labels in `label` typography / `ink-muted`. Inputs on `surface-raised`, `border`, focus ring `accent` (2px ring, visible in both modes). Helper text `ink-muted`, never below 4.5:1 on its background.

**Tables / lists:** Row hover `surface` shift; avoid wrapping every row in a card. Dividers use `border`, not gap-only whitespace.

**Alerts:** Full-width tinted bar (danger/success), no side-stripe callouts.

**Theme toggle (when built):** Compact control in nav: System / Light / Dark; icon + text or segmented control; persists preference without flash (inline script or `data-theme` before paint).

### Sidecar (not in frontmatter)

- Focus: `outline: 2px solid var(--accent); outline-offset: 2px`
- Transition: `color, background-color, border-color 150ms ease-out` (disabled when reduced motion)
- Nav max width: `max-w-6xl` centered; main padding `spacing.lg` vertical, `spacing.md` horizontal

## Do's and Don'ts

**Do**

- Use semantic CSS variables for all theme-aware colors.
- Test contrast in both modes for body text, placeholders, and muted labels.
- Keep club detail editors dense: collapsible sections over nested cards.
- Honor `prefers-reduced-motion` on any new animation.

**Don't**

- Reintroduce `gray-950` + `indigo-600` as the default pairing.
- Add marketing gradients, hero metrics, or uppercase section eyebrows.
- Stack identical bordered cards for every form block.
- Use `gray-400`/`gray-500` on colored surfaces without checking contrast.
- Gate content visibility on entrance animations.

**Implementation:** Semantic tokens live in `dashboard/src/index.css` (`@theme` + CSS variables). Components use `bg-bg`, `text-ink`, `bg-accent`, etc. Shared patterns: `btn-primary`, `input-field`, `panel`, `alert-danger` in the same file.
