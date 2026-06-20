release: python scripts/heroku_release.py
web: uvicorn api.app:app --host 0.0.0.0 --port $PORT
worker: python run_bot.py
cashier: python run_cashier.py
notification: python run_notification_bot.py
