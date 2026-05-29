# Product

## Register

product

## Users

Club operators and internal GG staff share the same GG Dashboard. Operators configure their club bot (payment methods, welcome copy, groups, cooldowns) often under time pressure during live player support. Staff manage many clubs and need fast scanning, comparison, and bulk edits without hunting through nested UI.

Context is typically a desktop browser at a desk, sometimes mid-incident, with PostgreSQL-backed config that must stay accurate because mistakes flow straight to Telegram players.

## Product Purpose

GG Support Bot gives poker clubs a configurable Telegram support experience: deposits, cashouts, custom commands, group linking, broadcasts, and staff tooling (GGCashier, `/gc` megagroups). The dashboard is the control plane for that behavior.

Success means operators can find and change the right setting quickly, preview how players will see it, and trust that what they saved is what the bot will send.

## Brand Personality

Fast, dense, no-nonsense. The UI should feel like a capable operations console: information-forward, low ceremony, no decorative chrome that slows repeat tasks. Confidence comes from clarity and predictable structure, not from marketing polish.

## Anti-references

- Generic dark SaaS (gray-950 canvas, indigo accent everywhere, identical bordered cards for every block)
- Flashy marketing patterns (gradient heroes, glassmorphism, gradient text, metric callouts)
- Consumer-playful UI (oversized rounding, illustration-heavy empty states, bubbly copy)
- Telegram chat-app mimicry (we configure the bot; we are not the messenger)

## Design Principles

1. **Density earns trust for repeat users.** Prefer scannable tables, tight vertical rhythm, and inline actions over card stacks when the task is operational.
2. **Hierarchy follows the job.** Club list, club detail, and nested editors (methods, variants, tiers) should read as clear levels; never flatten everything into same-weight panels.
3. **Configure with confidence.** Labels, helper text, and previews should make player-facing outcomes obvious before save (especially payment flows and message templates).
4. **Speed over spectacle.** Every screen should answer "what can I do here?" in one glance; decoration that does not aid the task is removed.
5. **Mixed audience, one shell.** Operator self-serve and staff multi-club workflows share navigation; differentiate through copy and defaults where mental models diverge, not duplicate layouts.

## Accessibility & Inclusion

Target WCAG 2.1 AA for contrast, visible focus, and keyboard reachability on interactive controls. Respect `prefers-reduced-motion`: use instant state changes or subtle opacity crossfades instead of motion-heavy entrances on dashboard surfaces.
