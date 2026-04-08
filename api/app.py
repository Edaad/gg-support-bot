import os
import pathlib

from fastapi import FastAPI
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
    from api.routes.methods import router as methods_router
    from api.routes.sub_options import router as sub_options_router
    from api.routes.commands import router as commands_router
    from api.routes.tiers import router as tiers_router
    from api.routes.simulate import router as simulate_router
    from api.routes.broadcast import router as broadcast_router
    from api.routes.variants import router as variants_router
    from api.routes.broadcast_groups import router as broadcast_groups_router

    app.include_router(clubs_router)
    app.include_router(methods_router)
    app.include_router(sub_options_router)
    app.include_router(tiers_router)
    app.include_router(commands_router)
    app.include_router(simulate_router)
    app.include_router(broadcast_router)
    app.include_router(variants_router)
    app.include_router(broadcast_groups_router)

    # ── Serve React dashboard (production build) ─────────────────────────
    dist_dir = pathlib.Path(__file__).resolve().parent.parent / "dashboard" / "dist"
    if dist_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(dist_dir / "assets")), name="assets")

        @app.get("/{full_path:path}")
        def serve_spa(full_path: str):
            file = dist_dir / full_path
            if file.is_file():
                return FileResponse(str(file))
            return FileResponse(str(dist_dir / "index.html"))

    return app


app = create_app()
