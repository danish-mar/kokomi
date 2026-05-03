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


@app.get("/health")
async def health_check():
    """Lightweight health probe for Docker/orchestrators."""
    return JSONResponse({"status": "ok"}, status_code=200)


app.mount("/static", StaticFiles(directory="public/static"), name="static")
app.mount("/images", StaticFiles(directory="public/images"), name="images")
app.mount("/avatars", StaticFiles(directory="data/avatars"), name="avatars")


# Register all routers
from app.routers import pages, prefs, mcp_servers, characters, conversations, chat, voice, spaces, whatsapp, workflows  # noqa: E402

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
