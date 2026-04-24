from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI(title="Kokomi AI")
templates = Jinja2Templates(directory="templates")

app.mount("/static", StaticFiles(directory="public/static"), name="static")
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
