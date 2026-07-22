# Popup reply keyboard (player `/deposit` / `/cashout`)

Per-club toggle **Enable pop up keyboard** on the dashboard (main bot, default off).
TestGGSupportBot (`run_test_bot.py` / `BOT_TEST_WORKER=1`) always has the feature on for eligible groups.

Players get a selective persistent reply keyboard with **`/deposit`** and **`/cashout`**. Any other player message (text or media) while the keyboard is installed silently removes it.

Install posts: *Looks like your request was handled…*  
Free-text strip posts: *We'll be with you in just a second.*  
Both stay in chat (deleting the carrier message clears the keyboard on clients).

Installed state is stored on `support_group_chats.popup_keyboard_installed` (main bot).
**TestGGSupportBot** keeps that flag **in memory** only (no DB write). Player TG id upsert is skipped on the test bot (unique club+player constraint); targeting uses chat_data.

Groups without a `support_group_chats` row: main bot skips install; test bot can still
install when the player id is known from recent chat activity.

## Migrations

```bash
# Club toggle (if not already applied)
DATABASE_URL=... python migrate_enable_popup_keyboard.py

# Durable installed flag
DATABASE_URL=... python migrate_popup_keyboard_installed.py
```

## Single-group verification (TestGGSupportBot)

Use **one** support group that already has a ClubGG player id in the title (`SHORTHAND / ID / …`).

1. Start `python run_test_bot.py` (do not also run the main worker on the same groups).
2. As the **player** (not a `/gc` support account), send any message in that group.
3. Wait **30 seconds** (test bot) / **5 minutes** (main bot) with no further human messages → install copy appears and the **`/deposit`** / **`/cashout`** keyboard stays (player only; check phone and desktop).
4. Tap **`/deposit`** → flow starts; keyboard removed on the amount prompt; complete or `/cancel` → after another quiet period, keyboard returns.
5. With keyboard up, send free text or a photo → *We'll be with you in just a second.*; keyboard removed; after quiet period, keyboard returns.
6. Confirm a support/AM account does **not** get the reply keyboard.
