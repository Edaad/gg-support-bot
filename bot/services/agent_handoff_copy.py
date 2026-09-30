"""Player-facing lines the bot posts when an automated flow hands off to a human.

Senders import these constants so the response audit can recognise a bot
hand-off by exact text instead of matching prose.
"""

from __future__ import annotations

# Automated cashout (bot/handlers/cashout.py) and chip transfer
# (bot/handlers/transfer.py) bow out with this line.
AGENT_SHORTLY_COPY = "An agent will be with you shortly."

# Early feeback (/earlyrb, bot/services/early_rakeback_auto.py) escalations.
ADMIN_SHORTLY_COPY = "An Admin will be with you shortly."

AGENT_HANDOFF_LINES: tuple[str, ...] = (AGENT_SHORTLY_COPY, ADMIN_SHORTLY_COPY)
