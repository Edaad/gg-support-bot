# Popup reply keyboard (player Deposit / Cashout / Other)

Per-club toggle **Enable pop up keyboard** on the dashboard (main bot, default off).
TestGGSupportBot (`run_test_bot.py` / `BOT_TEST_WORKER=1`) always has the feature on for eligible groups.

## Migration

```bash
DATABASE_URL=... python migrate_enable_popup_keyboard.py
```

## Single-group verification (TestGGSupportBot)

Use **one** support group that already has a ClubGG player id in the title (`SHORTHAND / ID / …`).

1. Start `python run_test_bot.py` (do not also run the main worker on the same groups).
2. As the **player** (not a `/gc` support account), send any message in that group.
3. Wait **5 minutes** with no further human messages → bot should post the install line and show **Deposit / Cashout / Other** (player only).
4. Tap **Deposit** (or `/deposit`) → keyboard removed; complete or `/cancel` → after another 5 min quiet, keyboard returns.
5. Tap **Other** → ack + keyboard removed; after 5 min quiet, keyboard returns.
6. Confirm a support/AM account does **not** get the reply keyboard.
