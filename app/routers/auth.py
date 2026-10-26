from datetime import timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.auth import (
    authenticate_user_async,
    create_access_token,
    get_user_by_username_async,
    get_current_user,
    JWT_SECRET_KEY,
    JWT_ALGORITHM,
)
from app.config import ACCESS_TOKEN_EXPIRE_MINUTES, REMEMBER_ME_DAYS
from app.storage import load_prefs

router = APIRouter(prefix="/auth", tags=["auth"])

def inject_prefs(request: Request):
    return {"prefs": load_prefs(), "current_user": getattr(request.state, "user", None)}

templates = Jinja2Templates(directory="templates", context_processors=[inject_prefs])


@router.get("/me")
async def get_me(request: Request):
    """Return the profile and role of the currently authenticated user."""
    try:
        user = await get_current_user(request)
        return {
            "authenticated": True,
            "user": {
                "id": user["id"],
                "username": user["username"],
                "role": user["role"],
                "display_name": user.get("display_name") or user["username"],
                "created_at": user.get("created_at"),
            },
        }
    except Exception:
        return JSONResponse(status_code=401, content={"authenticated": False, "user": None})


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Serve the login page. Redirects to workspace if already authenticated unless switching accounts."""
    if not request.query_params.get("switch") and not request.query_params.get("error"):
        token = request.cookies.get("access_token")
        if token:
            try:
                from jose import jwt
                payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
                username = payload.get("sub")
                if username:
                    user = await get_user_by_username_async(username)
                    if user:
                        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
            except Exception:
                pass
    return templates.TemplateResponse(request, "login.html", {"request": request})


@router.post("/login")
async def login(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    remember: Optional[str] = Form(None)
):
    """Handle multi-user login with Remember Me support and AJAX compatibility."""
    is_json = (
        "application/json" in request.headers.get("accept", "")
        or request.headers.get("x-requested-with") == "XMLHttpRequest"
    )

    user = await authenticate_user_async(username, password)
    if not user:
        if is_json:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"ok": False, "error": "Can't login"}
            )
        return RedirectResponse(url="/auth/login?error=Invalid+credentials", status_code=status.HTTP_303_SEE_OTHER)
    
    is_remember = bool(remember and remember.lower() in ("true", "on", "1", "yes"))

    if is_remember:
        expires_delta = timedelta(days=REMEMBER_ME_DAYS)
        max_age = REMEMBER_ME_DAYS * 24 * 3600
    else:
        expires_delta = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        max_age = ACCESS_TOKEN_EXPIRE_MINUTES * 60

    access_token = create_access_token(
        data={"sub": user["username"], "role": user.get("role", "Standard user"), "uid": user.get("id")},
        expires_delta=expires_delta
    )
    
    if is_json:
        res = JSONResponse(content={"ok": True, "redirect": "/"})
    else:
        res = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    res.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        max_age=max_age,
        expires=max_age,
        samesite="lax",
        secure=False, # Set to True in production with HTTPS
    )
    return res


@router.get("/logout")
async def logout(response: Response):
    """Handle logout."""
    res = RedirectResponse(url="/auth/login?switch=1", status_code=status.HTTP_303_SEE_OTHER)
    res.delete_cookie(key="access_token", path="/")
    return res
