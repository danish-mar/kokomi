from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from app.storage import load_prefs

router = APIRouter()
def inject_prefs(request: Request):
    return {"prefs": load_prefs()}

templates = Jinja2Templates(directory="templates", context_processors=[inject_prefs])

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