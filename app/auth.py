import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyCookie
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    AUTH_PASSWORD,
    AUTH_USERNAME,
    JWT_ALGORITHM,
    JWT_SECRET_KEY,
)

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Cookie authentication scheme
cookie_sec = APIKeyCookie(name="access_token", auto_error=False)

def verify_password(plain_password, hashed_password):
    """Verify a plain password against its hash."""
    # Since we are using a single password from .env for simplicity, 
    # we can also just compare strings if it's not hashed in .env.
    # But for future flexibility, we'll use passlib.
    if plain_password == hashed_password:
        return True
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception:
        return False

def get_password_hash(password):
    """Hash a password."""
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Create a new JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return encoded_jwt

async def get_current_user(request: Request, token: Optional[str] = Depends(cookie_sec)):
    """
    Dependency to get the current user from the JWT token in the cookie.
    If the token is missing or invalid, raises 401.
    """
    if not token:
        # Check if it's an HTMX request or a regular page request
        if "text/html" in request.headers.get("Accept", ""):
            # For page requests, we might want to redirect to /login
            # But here we just raise 401 and handle it in the middleware or router
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        username: str = payload.get("sub")
        
        from app.storage import load_prefs
        prefs = load_prefs()
        configured_username = prefs.get("admin_username", "admin")
        
        if username is None or username != configured_username:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
            )
        return username
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

def authenticate_user(username, password):
    """Authenticate a user against the configured credentials."""
    from app.storage import load_prefs
    prefs = load_prefs()
    configured_username = prefs.get("admin_username", "admin")
    configured_password = prefs.get("admin_password", "admin")

    if username != configured_username:
        return False
    if not verify_password(password, configured_password):
        return False
    return True
