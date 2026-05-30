from fastapi import APIRouter, Depends, HTTPException, Request, Response, status, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import authenticate_user, create_access_token
from app.config import ACCESS_TOKEN_EXPIRE_MINUTES

from app.storage import load_prefs

router = APIRouter(prefix="/auth", tags=["auth"])

def inject_prefs(request: Request):
    return {"prefs": load_prefs()}

templates = Jinja2Templates(directory="templates", context_processors=[inject_prefs])

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Serve the login page."""
    # Check if user is already logged in
    # (Optional: redirect to index if token exists and is valid)
    return templates.TemplateResponse(request, "login.html", {"request": request})

@router.post("/login")
async def login(
    response: Response,
    username: str = Form(...),
    password: str = Form(...)
):
    """Handle login form submission."""
    if not authenticate_user(username, password):
        return RedirectResponse(url="/auth/login?error=Invalid+credentials", status_code=status.HTTP_303_SEE_OTHER)
    
    access_token = create_access_token(data={"sub": username})
    
    # Set cookie
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        expires=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        samesite="lax",
        secure=False, # Set to True in production with HTTPS
    )
    return response

@router.get("/logout")
async def logout(response: Response):
    """Handle logout."""
    response = RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(key="access_token")
    return response
