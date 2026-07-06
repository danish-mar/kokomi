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

    yield
    # Shutdown — tear down MCP sessions and telegram polling
    scheduler_task.cancel()
    from app.routers.telegram import stop_polling
    await stop_polling()
    from app.mcp import teardown_pool
    await teardown_pool()


app = FastAPI(title="Kokomi AI", lifespan=lifespan)
templates = Jinja2Templates(directory="templates")

# ── Global Route Protection ──
from app.auth import get_current_user
from fastapi import Request
from fastapi.responses import RedirectResponse
from jose import JWTError

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    from app.storage import load_prefs
    prefs = load_prefs()
    setup_completed = prefs.get("setup_completed", False)

    # If setup is not completed, disable auth constraints globally so user can onboard
    if not setup_completed:
        response = await call_next(request)
        if request.url.path.startswith("/static/") or request.url.path.startswith("/images/") or request.url.path == "/favicon.ico":
            response.headers["Cache-Control"] = "public, max-age=604800, must-revalidate"
        return response

    # Public paths that don't require authentication
    public_paths = ["/auth/login", "/auth/logout", "/static", "/images", "/health", "/favicon.ico"]
    
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
    # Inject static content caching headers for all static files, images, and icons
    if path.startswith("/static/") or path.startswith("/images/") or path == "/favicon.ico":
        response.headers["Cache-Control"] = "public, max-age=604800, must-revalidate"
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
from app.routers import auth, pages, prefs, mcp_servers, characters, conversations, chat, voice, spaces, whatsapp, telegram, workflows, insights, app_store  # noqa: E402

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
