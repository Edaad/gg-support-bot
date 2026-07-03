# Issue reports — `/report` and `/reports`

Staff-only bot commands for centralized issue reporting. Reports go to Postgres and a dedicated Slack channel; nothing is posted in player support groups.

## Create a report

1. In a support group, send **`/escalate`** (silent — command deleted, no group reply)
2. Open **DM with GG Support** → tap **Continue report**
3. Wizard: **Notify** (who to ping) → **Title** → **Details** → **Evidence** (optional) → **Submit**

Or DM the bot directly: **`/report`**

### System-generated reports

Venmo ingest auto-creates issue reports when a payment is flagged **Goods & Services** (`goods_or_services: true` on the Zapier POST). These tickets notify **Head admin**, include payment details and **DO NOT ADD — refund required**, and appear in `/reports` like any other deposit report. No staff action is needed to open the ticket.

### Fields

| Field | Required |
|-------|----------|
| Notify | Head admin, Engineer, RB admin (pick at least one) |
| Title | Yes |
| Details | Yes |
| Evidence | No (up to 5 screenshots) |
| Group | Auto from group chat name when started in a support group |

## Triage (DM only)

| Command | Action |
|---------|--------|
| `/reports` | List open reports |
| `/reports resolved` | List recently resolved |
| `/reports 42` | Detail + buttons (Resolve, Edit, Add evidence) |
| `/reports 42 resolve` | Start resolve flow — solution text + optional screenshots |

## Slack routing

Issue reports use a **separate Slack app and channel** from general ops alerts (`SLACK_OPS_*`).

```bash
SLACK_ISSUE_REPORT_BOT_TOKEN=xoxb-...
SLACK_ISSUE_REPORT_CHANNEL_ID=C...
# optional fallback:
# SLACK_ISSUE_REPORT_WEBHOOK_URL=https://hooks.slack.com/services/...
```

Set `ISSUE_REPORT_TAG_MENTIONS` JSON to @mention the right audience in Slack:

```json
{
  "head_admin": "<!subteam^S_HEAD>",
  "engineer": "<!subteam^S_ENG>",
  "rb_admin": "<!subteam^S_RB>"
}
```

Slack messages show **For:** Head admin, Engineer, etc., plus subteam/user mentions when configured.

## Migrations

```bash
DATABASE_URL=... python migrate_issue_reports_v2.py
DATABASE_URL=... python migrate_issue_report_drafts.py
DATABASE_URL=... python migrate_issue_reports_resolve.py
```

Open tickets get a **Slack reminder every 2 hours** until resolved (thread reply when possible).

## Code

- Handlers: [`bot/handlers/issue_reports.py`](../bot/handlers/issue_reports.py)
- Service: [`bot/services/issue_reports.py`](../bot/services/issue_reports.py)
