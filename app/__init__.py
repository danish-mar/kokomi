from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates


@asynccontextmanager
async def lifespan(app):
    # Startup — pool will be lazily initialized on first request or splash
    yield
    # Shutdown — tear down MCP sessions
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
    # Public paths that don't require authentication
    public_paths = ["/auth/login", "/auth/logout", "/static", "/images", "/health", "/favicon.ico"]
    
    path = request.url.path
    
    # Check if path is public or starts with a public path (like /static/...)
    is_public = any(path == p or path.startswith(p + "/") for p in public_paths)
    
    if not is_public:
        try:
            # We use get_current_user logic here manually since it's a middleware
            from app.auth import cookie_sec, AUTH_USERNAME, JWT_SECRET_KEY, JWT_ALGORITHM
            from jose import jwt
            
            token = request.cookies.get("access_token")
            if not token:
                return RedirectResponse(url="/auth/login")
            
            payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
            username: str = payload.get("sub")
            if username is None or username != AUTH_USERNAME:
                return RedirectResponse(url="/auth/login")
                
        except (JWTError, Exception):
            return RedirectResponse(url="/auth/login")

    response = await call_next(request)
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
from app.routers import auth, pages, prefs, mcp_servers, characters, conversations, chat, voice, spaces, whatsapp, workflows, insights  # noqa: E402

app.include_router(auth.router)
app.include_router(pages.router)
app.include_router(prefs.router)
app.include_router(mcp_servers.router)
app.include_router(characters.router)
app.include_router(conversations.router)
app.include_router(chat.router)
app.include_router(voice.router)
app.include_router(spaces.router)
app.include_router(whatsapp.router)
app.include_router(workflows.router)
app.include_router(insights.router)
