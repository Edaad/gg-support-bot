import logging
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

from fastapi import FastAPI


def _configure_api_logging() -> None:
    """Send application logging to stderr on the web dyno (Heroku shows only uvicorn otherwise)."""

    root = logging.getLogger()
    level_name = (os.getenv("LOG_LEVEL") or "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)

    if root.handlers:
        root.setLevel(level)
        for handler in root.handlers:
            try:
                handler.setLevel(level)
            except Exception:
                pass
        return

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(level)


_configure_api_logging()
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from db.connection import init_engine
from db.models import Base

def create_app() -> FastAPI:
    app = FastAPI(title="GG Support Dashboard API", version="1.0.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    def on_startup():
        engine = init_engine()
        Base.metadata.create_all(engine)

    # ── Auth route (no token required) ────────────────────────────────────
    from api.auth import verify_password, create_token
    from api.schemas import LoginRequest, TokenResponse

    @app.post("/api/auth/login", response_model=TokenResponse)
    def login(body: LoginRequest):
        if not verify_password(body.password):
            from fastapi import HTTPException
            raise HTTPException(401, "Invalid password")
        return TokenResponse(token=create_token())

    # ── Protected API routers ─────────────────────────────────────────────
    from api.routes.clubs import router as clubs_router
    from api.routes.commands import router as commands_router
    from api.routes.simulate import router as simulate_router
    from api.routes.broadcast import router as broadcast_router
    from api.routes.broadcast_groups import router as broadcast_groups_router
    from api.routes.weekly_stats import router as weekly_stats_router
    from api.routes.weekly_stats_proxy import router as weekly_stats_proxy_router
    from api.routes.gc_mtproto import router as gc_mtproto_router
    from api.routes.bonus import router as bonus_router
    from api.routes.cashout_records import router as cashout_records_router
    from api.routes.payments import router as payments_router
    from api.routes.stripe_deposit import router as stripe_deposit_router
    from api.routes.venmo_payments import router as venmo_payments_router
    from api.routes.zelle_payments import router as zelle_payments_router
    from api.routes.cashapp_payments import router as cashapp_payments_router
    from api.routes.paypal_payments import router as paypal_payments_router
    from api.routes.crypto_payments import router as crypto_payments_router
    from api.routes.v2_payment import router as v2_payment_router
    from api.routes.issue_reports import router as issue_reports_router

    app.include_router(weekly_stats_proxy_router)
    app.include_router(stripe_deposit_router)
    app.include_router(venmo_payments_router)
    app.include_router(zelle_payments_router)
    app.include_router(cashapp_payments_router)
    app.include_router(paypal_payments_router)
    app.include_router(crypto_payments_router)
    app.include_router(v2_payment_router)
    app.include_router(clubs_router)
    app.include_router(commands_router)
    app.include_router(simulate_router)
    app.include_router(broadcast_router)
    app.include_router(broadcast_groups_router)
    app.include_router(weekly_stats_router)
    app.include_router(gc_mtproto_router)
    app.include_router(bonus_router)
    app.include_router(cashout_records_router)
    app.include_router(payments_router)
    app.include_router(issue_reports_router)

    # ── Serve React dashboard (production build) ─────────────────────────
    # Only mount if a real Vite build exists (dist/assets + index.html). Heroku/API-only
    # deploys often omit dashboard/dist — the API must still start.
    root = Path(__file__).resolve().parent.parent
    dist_dir = root / "dashboard" / "dist"
    assets_dir = dist_dir / "assets"
    index_html = dist_dir / "index.html"
    if assets_dir.is_dir() and index_html.is_file():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        @app.get("/")
        def serve_spa_root():
            """`/{full_path:path}` does not match GET / in FastAPI; root must be explicit."""
            return FileResponse(str(index_html))

        @app.get("/{full_path:path}")
        def serve_spa(full_path: str):
            # Do not treat /api/* as SPA routes — otherwise POSTs to unknown API paths
            # return 405 (GET exists) instead of 404/ hitting the real handler.
            if full_path == "api" or full_path.startswith("api/"):
                from fastapi import HTTPException

                raise HTTPException(404, "Not Found")
            file = dist_dir / full_path
            if file.is_file():
                return FileResponse(str(file))
            return FileResponse(str(index_html))

    return app


app = create_app()
