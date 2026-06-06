---
name: GG Dashboard
description: Fast, dense operator console with system light/dark, muted slate-blue accent, and semantic state colors.
colors:
  light-bg: "#f6f7f9"
  light-surface: "#eef0f4"
  light-surface-raised: "#ffffff"
  light-control: "#e8ebf0"
  light-control-hover: "#dfe3ea"
  light-ink: "#1a2230"
  light-ink-muted: "#4a5568"
  light-ink-faint: "#5c6578"
  light-border: "#d8dde6"
  light-accent: "#4a6fa5"
  light-accent-hover: "#3d5f8f"
  light-on-accent: "#f8f9fb"
  light-danger-bg: "#fce8e8"
  light-danger-ink: "#9b2c2c"
  light-success-bg: "#e6f4ea"
  light-success-ink: "#2d6a3e"
  light-warning-bg: "#fef3dc"
  light-warning-ink: "#8a5a00"
  dark-bg: "#12151c"
  dark-surface: "#1a1f2a"
  dark-surface-raised: "#222836"
  dark-control: "#2a3140"
  dark-control-hover: "#323a4c"
  dark-ink: "#e8ecf4"
  dark-ink-muted: "#9aa3b5"
  dark-ink-faint: "#7a8498"
  dark-border: "#2e3648"
  dark-accent: "#7a9fd4"
  dark-accent-hover: "#8eb0e0"
  dark-on-accent: "#12151c"
  dark-danger-bg: "#3a1f24"
  dark-danger-ink: "#f0a8a8"
  dark-success-bg: "#1f3328"
  dark-success-ink: "#8fd4a0"
  dark-warning-bg: "#3a3018"
  dark-warning-ink: "#e8c878"
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
  section-label:
    fontFamily: "{typography.display.fontFamily}"
    fontSize: "0.75rem"
    fontWeight: 500
    lineHeight: 1.4
    letterSpacing: "normal"
rounded:
  sm: "6px"
  md: "8px"
  lg: "12px"
  xl: "12px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "32px"
components:
  button-primary:
    backgroundColor: "{colors.dark-accent}"
    textColor: "{colors.dark-on-accent}"
    rounded: "{rounded.md}"
    padding: "10px 16px"
  button-primary-hover:
    backgroundColor: "{colors.dark-accent-hover}"
    textColor: "{colors.dark-on-accent}"
    rounded: "{rounded.md}"
    padding: "10px 16px"
  input-field:
    backgroundColor: "{colors.dark-surface-raised}"
    textColor: "{colors.dark-ink}"
    rounded: "{rounded.md}"
    padding: "10px 16px"
  nav-link-active:
    backgroundColor: "{colors.dark-control}"
    textColor: "{colors.dark-accent}"
    rounded: "{rounded.md}"
    padding: "8px 12px"
  chip-accent:
    backgroundColor: "{colors.dark-control}"
    textColor: "{colors.dark-accent}"
    rounded: "{rounded.sm}"
    padding: "4px 12px"
---

## Overview

GG Dashboard is an operations console for club bot configuration: clubs, payment methods, flows, analytics, and staff settings. Visual tone is **fast and dense**: tight spacing, clear type hierarchy, minimal chrome. Color is **restrained**: cool neutrals carry most surfaces; slate-blue accent marks primary actions, active navigation, and key metrics only.

**Theme behavior:** Respects `prefers-color-scheme` by default. Manual override (System / Light / Dark) via `ThemeToggle` in the nav; preference persists in `localStorage` on `html[data-theme]`. Implementation uses semantic CSS variables (`--bg`, `--surface`, `--ink`, `--accent`, etc.) swapped per color scheme.

**Motion:** Subtle transitions on hover/focus only (150ms ease-out). Under `prefers-reduced-motion: reduce`, transitions collapse to instant.

Canonical OKLCH references (implement in `dashboard/src/index.css`):

| Token | Light | Dark |
|-------|-------|------|
| bg | `oklch(0.98 0.006 250)` | `oklch(0.16 0.02 250)` |
| surface | `oklch(0.96 0.008 250)` | `oklch(0.20 0.02 250)` |
| surface-raised | `oklch(1 0 0)` | `oklch(0.24 0.02 250)` |
| control | `oklch(0.93 0.01 250)` | `oklch(0.28 0.02 250)` |
| ink | `oklch(0.25 0.03 250)` | `oklch(0.93 0.01 250)` |
| ink-muted | `oklch(0.38 0.025 250)` | `oklch(0.72 0.02 250)` |
| ink-faint | `oklch(0.48 0.02 250)` | `oklch(0.58 0.02 250)` |
| border | `oklch(0.88 0.01 250)` | `oklch(0.32 0.02 250)` |
| accent | `oklch(0.52 0.08 250)` | `oklch(0.72 0.08 250)` |
| success-ink | `oklch(0.42 0.12 145)` | `oklch(0.82 0.08 145)` |
| warning-ink | `oklch(0.48 0.12 85)` | `oklch(0.85 0.1 85)` |

## Colors

**Strategy:** Restrained tinted neutrals (hue ~250, low chroma) plus one slate-blue accent. Semantic success/warning/danger tokens for state only. No indigo-violet SaaS default, no warm cream body backgrounds, no gradient fills.

**Light mode:** Off-white cool background, white raised panels, ink text ≥4.5:1 on bg. Muted labels use `ink-muted`; tertiary copy uses `ink-faint`.

**Dark mode:** Deep blue-gray base (not pure black), raised surfaces one step lighter. Accent is lighter in dark mode for the same perceived weight.

**Semantic colors:** Danger, success, and warning use tinted backgrounds with readable ink. KPI tones map to semantic ink colors (`text-success-ink`, `text-warning-ink`, `text-accent`, `text-ink-muted`).

**Accent usage cap:** ≤10% of visible pixels on a typical screen (primary buttons, active nav, focus rings, key links, one accent KPI). Tables, forms, and metadata stay neutral.

## Typography

Single sans stack (system UI). Hierarchy through **weight and size**, not extra font families.

| Role | Size | Weight | Use |
|------|------|--------|-----|
| Display | 1.5rem | 700 | Page titles (`h1`) |
| Title | 1.125rem | 600 | Modal titles, occasional panel headers |
| Body | 0.875rem | 400 | Default copy, table cells |
| Label | 0.8125rem | 500 | Form labels, column headers |
| Section label | 0.75rem | 500 | In-panel section headers (`.section-label`) |

Body line length stays within the `max-w-6xl` content shell (~65–75ch in prose blocks). KPI values use `tabular-nums` for alignment.

## Elevation

Prefer **tonal layering** (bg → surface → surface-raised → control) over heavy shadows. Light mode: optional shadow on modals only (`shadow-xl` on dialog panel). Dark mode: borders (`border` token) define edges.

No glassmorphism, no gradient borders, no left accent stripes on alerts.

## Components

**App shell:** Sticky header on `surface`, 1px `border` bottom. Logo in `ink`; nav links `ink-muted` with `bg-accent/12 text-accent` for active route. Horizontal scroll nav on small screens with `nav-touch` min-height on coarse pointers. Skip link visible on focus.

**Theme toggle:** Segmented control (System / Light / Dark) in nav; active segment uses `accent` fill.

**Buttons:** Primary (`btn-primary`, `btn-primary-sm`), secondary bordered/ghost (`btn-secondary`, `btn-secondary-sm`), destructive (`btn-danger`, `btn-danger-outline`). Verb + object labels.

**Forms:** Labels via `label-field` / `label-field-xs`. Inputs via `input-field` / `input-field-sm`. Focus ring `accent`.

**Panels:** Single `.panel` per results region; avoid nested `.panel-nested` stacks. Use `border-t border-border pt-6` to separate sections inside one panel.

**Analytics / KPIs:** `KpiStat` component with optional drill-down (dotted underline on clickable values). Grid layout via `.kpi-grid` (`auto-fit`, min 8.5rem columns). Section headers use `.section-label` (quiet, muted). Breakdown counts use `.chip-neutral`, `.chip-accent`, `.chip-success`, `.chip-warning`. Drill-down lists open in `Modal` (native `<dialog>`), paginated at 50 rows.

**Tables:** `.table-scroll` wrapper with border; min-width 40rem for horizontal scroll on narrow viewports.

**Alerts:** `.alert-danger`, `.alert-success`, `.alert-warning` full-width tinted bars.

**Tabs:** `.tab-bar` with `.tab-active` / `.tab-inactive` or `.tab-active-accent` for accent-filled active tab.

### Sidecar (not in frontmatter)

- Focus: `focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2`
- Transition: `color, background-color, border-color 150ms ease-out` (disabled when reduced motion)
- Nav/content max width: `max-w-6xl` centered
- Z-index: header `z-40`, modal backdrop `z-50`, KPI tooltip `z-20`
- Route code-splitting: Analytics lazy-loaded via `React.lazy` + `Suspense`
- Chart tokens (`--chart-1` … `--chart-6`) reserved for future data viz; not yet used in UI

## Do's and Don'ts

**Do**

- Use semantic CSS variables / Tailwind theme tokens (`bg-bg`, `text-ink`, `bg-accent`, etc.).
- Test contrast in both modes for body text, placeholders, and muted labels.
- Keep club detail editors dense: collapsible sections over nested cards.
- Honor `prefers-reduced-motion` on any new animation.
- Use `.section-label` for in-panel section headers; let KPI values carry visual weight.

**Don't**

- Reintroduce hard-coded `slate-*` or `gray-*` Tailwind pairs on new screens.
- Add marketing gradients, hero-metric rows, or uppercase section eyebrows.
- Stack identical bordered cards for every form block.
- Duplicate the same data in an inline table and a drill-down modal.
- Gate content visibility on entrance animations.

**Implementation:** Semantic tokens live in `dashboard/src/index.css` (`@theme` + CSS variables). Shared patterns in `@layer components`. Reusable React components: `KpiStat`, `Modal`, `ThemeToggle`, `Layout`.
