from contextlib import asynccontextmanager
import logging

class WebSocketLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage().lower()
        if (
            "websocket" in msg or 
            "/ws/" in msg or 
            "connection open" in msg or 
            "connection closed" in msg or 
            "connection accepted" in msg or
            "accepted" in msg
        ):
            return False
        return True

# Apply filter to uvicorn loggers to silence websocket open/close output
for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
    logger = logging.getLogger(logger_name)
    logger.addFilter(WebSocketLogFilter())
    for handler in logger.handlers:
        handler.addFilter(WebSocketLogFilter())

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates


@asynccontextmanager
async def lifespan(app):
    # Startup — initialize SQLite DB tables, run legacy JSON auto-migration, download CDN vendor assets, start scheduler
    from app.db import init_db
    await init_db()

    from app.migration import auto_migrate_and_cleanup, migrate_embedding_model
    try:
        await auto_migrate_and_cleanup()
    except Exception as mig_err:
        logger.warning(f"Auto-migration failed during startup: {mig_err}")
    try:
        migrate_embedding_model()
    except Exception as emb_err:
        logger.warning(f"Embedding-model migration failed during startup: {emb_err}")

    from app.cdn import download_vendor_assets_if_missing
    try:
        await download_vendor_assets_if_missing()
    except Exception as e:
        logger.warning(f"Failed to download/verify CDN assets on startup: {e}")

    from app.scheduler import start_scheduler_loop
    import asyncio
    scheduler_task = asyncio.create_task(start_scheduler_loop())

    from app.storage import load_prefs
    from app.routers.telegram import start_polling
    _prefs = load_prefs()
    if _prefs.get("telegram_enabled") and not _prefs.get("telegram_use_webhook"):
        asyncio.create_task(start_polling())

    # Triton: broadcast a LAN discovery beacon so client moorings can auto-find us.
    try:
        from app.triton import manager as triton_manager
        app_port = int(os.getenv("PORT", "8000"))
        await triton_manager.start_discovery(app_port)
    except Exception as e:
        logger.warning(f"Triton discovery beacon failed to start: {e}")

    yield
    # Shutdown — tear down MCP sessions, telegram polling, Triton discovery
    scheduler_task.cancel()
    from app.routers.telegram import stop_polling
    await stop_polling()
    from app.mcp import teardown_pool
    await teardown_pool()
    try:
        from app.triton import manager as triton_manager
        await triton_manager.stop_discovery()
    except Exception:
        pass


app = FastAPI(title="Kokomi AI", lifespan=lifespan)
templates = Jinja2Templates(directory="templates")

# ── Global Route Protection ──
from app.auth import get_current_user
from fastapi import Request
from fastapi.responses import RedirectResponse
from jose import JWTError

def _static_cache_control(path: str):
    """Pick a Cache-Control policy for a static asset by path.

    Our own app code (`/static/js/`, `/static/css/`) is served `no-cache` so the
    browser revalidates it on every load — StaticFiles answers a cheap 304 when
    the file is unchanged, but the moment we ship a new build the browser fetches
    it immediately. Long-caching these (the previous 7-day max-age) was exactly
    why a freshly-deployed atlas.js/app.js kept being ignored: the browser ran
    stale cached JS against fresh server-rendered HTML (e.g. an atlas.html that
    calls startWorkflow() while the cached atlas.js had no such method).

    Vendored libraries, images and icons rarely change and are large, so they
    keep the long cache with revalidation.
    """
    if path.startswith("/static/js/") or path.startswith("/static/css/"):
        return "no-cache"
    if path.startswith("/static/") or path.startswith("/images/") or path == "/favicon.ico":
        return "public, max-age=604800, must-revalidate"
    return None


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    from app.storage import load_prefs
    prefs = load_prefs()
    setup_completed = prefs.get("setup_completed", False)

    # If setup is not completed, disable auth constraints globally so user can onboard
    if not setup_completed:
        response = await call_next(request)
        cc = _static_cache_control(request.url.path)
        if cc:
            response.headers["Cache-Control"] = cc
        return response

    # Public paths that don't require the admin cookie. The Triton agent WS
    # authenticates with its own per-device token (or the 8-digit pairing code),
    # so the browser-cookie check must not intercept it.
    public_paths = ["/auth/login", "/auth/logout", "/static", "/images", "/health",
                    "/favicon.ico",
                    # Triton client transports authenticate with a per-device token
                    # (or the 8-digit pairing code), not the admin cookie.
                    "/api/triton/agent", "/api/triton/enroll", "/api/triton/pair-status",
                    "/api/triton/poll", "/api/triton/result"]
    
    path = request.url.path
    
    # Check if path is public or starts with a public path (like /static/...)
    is_public = any(path == p or path.startswith(p + "/") for p in public_paths)
    
    if not is_public:
        try:
            # We use get_current_user logic here manually since it's a middleware
            from app.auth import JWT_SECRET_KEY, JWT_ALGORITHM
            from jose import jwt
            
            token = request.cookies.get("access_token")
            if not token:
                return RedirectResponse(url="/auth/login")
            
            payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
            username: str = payload.get("sub")
            
            configured_username = prefs.get("admin_username", "admin")
            if username is None or username != configured_username:
                return RedirectResponse(url="/auth/login")
                
        except (JWTError, Exception):
            return RedirectResponse(url="/auth/login")

    response = await call_next(request)
    # Inject static content caching headers (short for our own JS/CSS so deploys
    # are picked up immediately; long for vendored libs, images, and icons).
    cc = _static_cache_control(path)
    if cc:
        response.headers["Cache-Control"] = cc
    return response

@app.get("/health")
async def health_check():
    """Lightweight health probe for Docker/orchestrators."""
    return JSONResponse({"status": "ok"}, status_code=200)


import os
os.makedirs("data/uploads", exist_ok=True)
os.makedirs("data/avatars", exist_ok=True)

app.mount("/static", StaticFiles(directory="public/static"), name="static")
app.mount("/images", StaticFiles(directory="public/images"), name="images")
app.mount("/avatars", StaticFiles(directory="data/avatars"), name="avatars")
app.mount("/uploads", StaticFiles(directory="data/uploads"), name="uploads")


# Register all routers
from app.routers import auth, pages, prefs, mcp_servers, characters, conversations, chat, voice, spaces, whatsapp, telegram, workflows, insights, app_store, triton, canvas  # noqa: E402

app.include_router(auth.router)
app.include_router(pages.router)
app.include_router(prefs.router)
app.include_router(app_store.router)
app.include_router(mcp_servers.router)
app.include_router(characters.router)
app.include_router(conversations.router)
app.include_router(chat.router)
app.include_router(voice.router)
app.include_router(spaces.router)
app.include_router(whatsapp.router)
app.include_router(telegram.router)
app.include_router(workflows.router)
app.include_router(insights.router)
app.include_router(triton.router)
app.include_router(canvas.router)
