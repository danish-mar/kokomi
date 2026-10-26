from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from app.storage import load_prefs
from app.config import VERSION, RELEASE_NAME

router = APIRouter()
def inject_prefs(request: Request):
    user = getattr(request.state, "user", None)
    safe_user = None
    prefs = load_prefs().copy()
    if user:
        if isinstance(user, dict):
            safe_user = {k: v for k, v in user.items() if k != "password_hash"}
        else:
            safe_user = {k: getattr(user, k) for k in ["id", "username", "display_name", "role", "avatar", "created_at"] if hasattr(user, k)}
        prefs["user_avatar"] = safe_user.get("avatar") or None
        prefs["user_name"] = safe_user.get("display_name") or safe_user.get("username")
    else:
        prefs["user_avatar"] = None
    return {
        "prefs": prefs,
        "current_user": safe_user,
    }

def inject_app_version(request: Request):
    return {"app_version": {"version": VERSION, "release_name": RELEASE_NAME}}

templates = Jinja2Templates(directory="templates", context_processors=[inject_prefs, inject_app_version])

def check_setup():
    prefs = load_prefs()
    return prefs.get("setup_completed", False)

@router.get("/")
async def root(request: Request):
    if not check_setup():
        return RedirectResponse(url="/onboarding")
    return templates.TemplateResponse(request, "index.html", {"request": request})


@router.get("/settings")
async def settings_page(request: Request):
    if not check_setup():
        return RedirectResponse(url="/onboarding")
    return templates.TemplateResponse(request, "settings.html", {"request": request})


@router.get("/call")
async def call_page(request: Request):
    if not check_setup():
        return RedirectResponse(url="/onboarding")
    return templates.TemplateResponse(request, "call.html", {"request": request})

@router.get("/spaces")
async def spaces_page(request: Request):
    if not check_setup():
        return RedirectResponse(url="/onboarding")
    return templates.TemplateResponse(request, "spaces.html", {"request": request})

@router.get("/whatsapp")
async def whatsapp_page(request: Request):
    if not check_setup():
        return RedirectResponse(url="/onboarding")
    return templates.TemplateResponse(request, "whatsapp.html", {"request": request})

@router.get("/app-store")
async def app_store_page(request: Request):
    if not check_setup():
        return RedirectResponse(url="/onboarding")
    return templates.TemplateResponse(request, "app-store/index.html", {"request": request})

@router.get("/insights")
async def insights_page(request: Request):
    if not check_setup():
        return RedirectResponse(url="/onboarding")
    return templates.TemplateResponse(request, "insights.html", {"request": request})

@router.get("/memories")
async def memories_page(request: Request):
    if not check_setup():
        return RedirectResponse(url="/onboarding")
    return templates.TemplateResponse(request, "memories.html", {"request": request})


@router.get("/atlas")
async def atlas_page(request: Request):
    if not check_setup():
        return RedirectResponse(url="/onboarding")
    return templates.TemplateResponse(request, "atlas.html", {"request": request})

@router.get("/onboarding")
async def onboarding_page(request: Request):
    if check_setup():
        return RedirectResponse(url="/")
    return templates.TemplateResponse(request, "onboarding.html", {"request": request})