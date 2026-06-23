# Issue reports — `/report` and `/reports`

Staff-only bot commands for centralized issue reporting. Reports go to Postgres and Slack ops; nothing is posted in player support groups.

## Create a report

1. In a support group, send **`/escalate`** (silent — command deleted, no group reply)
2. Open **DM with GG Support** → tap **Continue report**
3. Wizard: **Category** → **Notify** (who to ping) → **Title** → **Details** → **Evidence** (optional) → **Submit**

Or DM the bot directly: **`/report`**

### Fields

| Field | Required |
|-------|----------|
| Category | Deposit, Cashout, Bot issue, Rakeback, Other |
| Notify | Head admin, Engineer, RB admin (defaults suggested per category) |
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
| `/reports 42 resolve` | Resolve report #42 |

## Slack routing

Set `ISSUE_REPORT_TAG_MENTIONS` JSON for audience mentions:

```json
{
  "head_admin": "<!subteam^S_HEAD>",
  "engineer": "<!subteam^S_ENG>",
  "rb_admin": "<!subteam^S_RB>"
}
```

## Migrations

```bash
DATABASE_URL=... python migrate_issue_reports_v2.py
DATABASE_URL=... python migrate_issue_report_drafts.py
```

## Code

- Handlers: [`bot/handlers/issue_reports.py`](../bot/handlers/issue_reports.py)
- Service: [`bot/services/issue_reports.py`](../bot/services/issue_reports.py)
