from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/")
async def root(request: Request):
    return templates.TemplateResponse(request, "index.html", {"request": request})


@router.get("/settings")
async def settings_page(request: Request):
    return templates.TemplateResponse(request, "settings.html", {"request": request})


@router.get("/call")
async def call_page(request: Request):
    return templates.TemplateResponse(request, "call.html", {"request": request})

@router.get("/spaces")
async def spaces_page(request: Request):
    return templates.TemplateResponse(request, "spaces.html", {"request": request})

@router.get("/whatsapp")
async def whatsapp_page(request: Request):
    return templates.TemplateResponse(request, "whatsapp.html", {"request": request})
