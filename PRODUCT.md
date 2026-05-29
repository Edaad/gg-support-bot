# Product

## Register

product

## Users

Club operators and support staff who run Telegram-based player support for poker clubs. They work in short, focused sessions: configuring deposit and cashout flows, editing bot responses, managing payment method variants, running broadcasts, and testing player-facing flows before they go live. Context is operational, not exploratory. Mistakes affect real money and real players.

## Product Purpose

GG Dashboard is the admin control plane for the GG Support Bot ecosystem. Operators use it to configure clubs, payment methods, custom commands, linked accounts, and broadcast groups without touching code. Success means an operator can find the right setting quickly, make a change confidently, and verify the player experience (via flow simulator) before players see it.

The dashboard serves the bots; it is not player-facing. Clarity and reliability matter more than brand spectacle.

## Brand Personality

**Practical, precise, calm.**

Voice is direct and operational: labels say what they do, errors say what went wrong, confirmations are explicit. The interface should feel like a well-organized control room: dark, low-glare, information-dense without clutter. Operators should feel competent and in control, not marketed to.

Emotional goal: quiet confidence. The tool disappears into the workflow.

## Anti-references

- Generic SaaS marketing dashboards (hero metrics, gradient accents, eyebrow labels on every section)
- Overly playful or gamified admin UIs that undermine trust around financial operations
- Light-mode "friendly startup" aesthetics that feel wrong for late-night ops work
- Dense data tables with no hierarchy, empty states, or error recovery
- Player-facing Telegram bot copy tone bleeding into admin labels (too casual, too emoji-heavy)

## Design Principles

1. **Task-first navigation.** Every screen answers "what am I doing here?" before "what can I click?" Primary actions are obvious; destructive actions require confirmation.
2. **Configure, then verify.** Editing flows and testing them belong in the same mental model. Changes should feel reversible until explicitly saved or broadcast.
3. **Financial gravity.** Payment methods, tiers, and variants are high-stakes. Use clear labels, visible state, and explicit save/error feedback. Never hide failure modes.
4. **Density with breathing room.** Operators manage many clubs and methods. Show enough context to work without paging, but avoid nested cards and visual noise.
5. **Consistent operator language.** Use the same terms the bots and staff already use (club, deposit, cashout, variant, tier). Avoid abstract product jargon.

## Accessibility & Inclusion

- Target WCAG 2.1 AA for contrast, focus states, and form labels
- Support keyboard navigation for all interactive controls (tabs, editors, modals)
- Respect `prefers-reduced-motion` for any transitions or loading states
- Error messages must be readable and specific, not color-only indicators
- No known requirements beyond standard WCAG; revisit if operator accessibility needs are identified
