import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyCookie
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select, func, or_

from app.config import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    AUTH_PASSWORD,
    AUTH_USERNAME,
    JWT_ALGORITHM,
    JWT_SECRET_KEY,
)
from app.db import _session, UserRow, SharedApiKeyRow, j_dumps, j_loads

# Roles definition: exactly two roles
ROLE_ENDMINISTRATOR = "Endministrator"
ROLE_STANDARD = "Standard user"
VALID_ROLES = [ROLE_ENDMINISTRATOR, ROLE_STANDARD]

import bcrypt

# Cookie authentication scheme
cookie_sec = APIKeyCookie(name="access_token", auto_error=False)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against its hash or plaintext fallback."""
    if not plain_password or not hashed_password:
        return False
    if plain_password == hashed_password:
        return True
    try:
        pwd_bytes = plain_password.encode("utf-8")[:72]
        hash_bytes = hashed_password.encode("utf-8")
        return bcrypt.checkpw(pwd_bytes, hash_bytes)
    except Exception:
        return False


def get_password_hash(password: str) -> str:
    """Hash a password with native bcrypt."""
    pwd_bytes = (password or "").encode("utf-8")[:72]
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a new JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


async def seed_initial_users() -> None:
    """Ensure at least one Endministrator user exists on first startup."""
    try:
        from app.storage import load_prefs
        prefs = load_prefs()
        async with _session() as sess:
            res = await sess.execute(select(func.count(UserRow.id)))
            count = res.scalar() or 0
            if count == 0:
                admin_user = (prefs.get("admin_username") or AUTH_USERNAME or "admin").strip().lower()
                admin_pass = prefs.get("admin_password") or AUTH_PASSWORD or "admin"
                admin_avatar = prefs.get("user_avatar")
                now = datetime.now(timezone.utc).isoformat()
                
                root_user = UserRow(
                    id=str(uuid.uuid4()),
                    username=admin_user,
                    password_hash=get_password_hash(admin_pass),
                    role=ROLE_ENDMINISTRATOR,
                    display_name=admin_user.title(),
                    avatar=admin_avatar,
                    created_at=now,
                    updated_at=now,
                )
                sess.add(root_user)
            else:
                admin_user = (prefs.get("admin_username") or AUTH_USERNAME or "denizuh").strip().lower()
                admin_avatar = prefs.get("user_avatar")
                if admin_avatar:
                    res = await sess.execute(select(UserRow).where(func.lower(UserRow.username) == admin_user))
                    u = res.scalar_one_or_none()
                    if u and not u.avatar:
                        u.avatar = admin_avatar
    except Exception as e:
        # Table might not exist or connection failed, safe to catch
        pass


async def get_user_by_username_async(username: str) -> Optional[dict]:
    """Retrieve user dictionary by username."""
    if not username:
        return None
    clean = username.strip().lower()
    async with _session() as sess:
        res = await sess.execute(select(UserRow).where(func.lower(UserRow.username) == clean))
        u = res.scalar_one_or_none()
        if not u:
            return None
        return {
            "id": u.id,
            "username": u.username,
            "password_hash": u.password_hash,
            "role": u.role,
            "display_name": u.display_name or u.username,
            "avatar": getattr(u, "avatar", None),
            "created_at": u.created_at,
            "updated_at": u.updated_at,
        }


async def get_user_by_id_async(user_id: str) -> Optional[dict]:
    """Retrieve user dictionary by user ID."""
    if not user_id:
        return None
    async with _session() as sess:
        u = await sess.get(UserRow, user_id)
        if not u:
            return None
        return {
            "id": u.id,
            "username": u.username,
            "password_hash": u.password_hash,
            "role": u.role,
            "display_name": u.display_name or u.username,
            "avatar": getattr(u, "avatar", None),
            "created_at": u.created_at,
            "updated_at": u.updated_at,
        }


async def authenticate_user_async(username: str, password: str) -> Optional[dict]:
    """Authenticate a user against the users table, with fallback to legacy preferences."""
    user = await get_user_by_username_async(username)
    if not user:
        # Fallback to configured preferences if users table was not yet seeded
        from app.storage import load_prefs
        prefs = load_prefs()
        cfg_user = (prefs.get("admin_username") or "admin").strip().lower()
        cfg_pass = prefs.get("admin_password") or "admin"
        if username.strip().lower() == cfg_user and verify_password(password, cfg_pass):
            return {
                "id": "root-default",
                "username": cfg_user,
                "role": ROLE_ENDMINISTRATOR,
                "display_name": cfg_user.title(),
            }
        return None
    
    if not verify_password(password, user["password_hash"]):
        return None
    return user


def authenticate_user(username: str, password: str) -> bool:
    """Synchronous check against legacy credentials (kept for fallback)."""
    from app.storage import load_prefs
    prefs = load_prefs()
    cfg_user = (prefs.get("admin_username") or "admin").strip().lower()
    cfg_pass = prefs.get("admin_password") or "admin"
    if username.strip().lower() == cfg_user and verify_password(password, cfg_pass):
        return True
    return False


async def get_current_user(request: Request, token: Optional[str] = Depends(cookie_sec)) -> dict:
    """
    Dependency to get the authenticated user from JWT cookie or Authorization header.
    Raises 401 if token is missing or invalid.
    """
    if not isinstance(token, str) or not token.strip():
        token = request.cookies.get("access_token")

    if not token:
        auth_hdr = request.headers.get("Authorization")
        if auth_hdr and auth_hdr.startswith("Bearer "):
            token = auth_hdr.split(" ", 1)[1]

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        username: str = payload.get("sub")
        if not username:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        
        user = await get_user_by_username_async(username)
        if not user:
            # Fallback to legacy prefs
            from app.storage import load_prefs
            prefs = load_prefs()
            cfg_user = (prefs.get("admin_username") or "admin").strip().lower()
            if username.strip().lower() == cfg_user:
                return {
                    "id": "root-default",
                    "username": cfg_user,
                    "role": ROLE_ENDMINISTRATOR,
                    "display_name": cfg_user.title(),
                }
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
        
        return user
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


async def require_endministrator(current_user: dict = Depends(get_current_user)) -> dict:
    """FastAPI dependency: requires current user to have the Endministrator role."""
    if current_user.get("role") != ROLE_ENDMINISTRATOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: Requires Endministrator role",
        )
    return current_user


# ─── User Management CRUD (Endministrator) ───────────────────────────────────

async def list_users_async() -> list[dict]:
    """List all registered users without sensitive password hashes."""
    async with _session() as sess:
        res = await sess.execute(select(UserRow).order_by(UserRow.created_at.asc()))
        users = res.scalars().all()
        return [
            {
                "id": u.id,
                "username": u.username,
                "role": u.role,
                "display_name": u.display_name or u.username,
                "avatar": getattr(u, "avatar", None),
                "created_at": u.created_at,
                "updated_at": u.updated_at,
            }
            for u in users
        ]


async def create_user_async(
    username: str,
    password: str,
    role: str = ROLE_STANDARD,
    display_name: Optional[str] = None,
    avatar: Optional[str] = None,
) -> dict:
    """Create a new user with either Endministrator or Standard user role."""
    clean_user = username.strip().lower()
    if not clean_user or len(clean_user) < 2:
        raise ValueError("Username must be at least 2 characters")
    if not re.match(r"^[a-zA-Z0-9_\-\.]+$", clean_user):
        raise ValueError("Username may only contain letters, numbers, hyphens, and underscores")
    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role '{role}'. Must be one of: {', '.join(VALID_ROLES)}")
    if not password or len(password) < 4:
        raise ValueError("Password must be at least 4 characters")

    async with _session() as sess:
        existing = await sess.execute(select(UserRow).where(func.lower(UserRow.username) == clean_user))
        if existing.scalar_one_or_none():
            raise ValueError(f"Username '{clean_user}' is already taken")

        now = datetime.now(timezone.utc).isoformat()
        new_user = UserRow(
            id=str(uuid.uuid4()),
            username=clean_user,
            password_hash=get_password_hash(password),
            role=role,
            display_name=(display_name.strip() if display_name else clean_user.title()),
            avatar=avatar.strip() if avatar else None,
            created_at=now,
            updated_at=now,
        )
        sess.add(new_user)
        await sess.flush()
        return {
            "id": new_user.id,
            "username": new_user.username,
            "role": new_user.role,
            "display_name": new_user.display_name,
            "avatar": new_user.avatar,
            "created_at": new_user.created_at,
            "updated_at": new_user.updated_at,
        }


async def update_user_async(
    user_id: str,
    actor_username: str,
    password: Optional[str] = None,
    role: Optional[str] = None,
    display_name: Optional[str] = None,
    avatar: Optional[str] = None,
) -> dict:
    """Update a user's details, role, or password. Protects the last Endministrator."""
    async with _session() as sess:
        u = await sess.get(UserRow, user_id)
        if not u:
            raise ValueError("User not found")

        # Guard: If demoting an Endministrator, ensure another Endministrator exists
        if role and role != u.role:
            if role not in VALID_ROLES:
                raise ValueError(f"Invalid role. Must be one of: {', '.join(VALID_ROLES)}")
            if u.role == ROLE_ENDMINISTRATOR and role != ROLE_ENDMINISTRATOR:
                admin_count_res = await sess.execute(
                    select(func.count(UserRow.id)).where(UserRow.role == ROLE_ENDMINISTRATOR)
                )
                if (admin_count_res.scalar() or 0) <= 1:
                    raise ValueError("Cannot demote the only remaining Endministrator")
            u.role = role

        if password and password.strip():
            if len(password.strip()) < 4:
                raise ValueError("Password must be at least 4 characters")
            u.password_hash = get_password_hash(password.strip())

        if display_name is not None:
            u.display_name = display_name.strip() or u.username

        if avatar is not None:
            u.avatar = avatar.strip() if avatar and avatar.strip() else None

        u.updated_at = datetime.now(timezone.utc).isoformat()
        await sess.flush()
        return {
            "id": u.id,
            "username": u.username,
            "role": u.role,
            "display_name": u.display_name,
            "avatar": u.avatar,
            "created_at": u.created_at,
            "updated_at": u.updated_at,
        }


async def delete_user_async(user_id: str, actor_username: str) -> bool:
    """Delete a user. Protects against self-deletion and deleting the only Endministrator."""
    async with _session() as sess:
        u = await sess.get(UserRow, user_id)
        if not u:
            raise ValueError("User not found")

        clean_actor = actor_username.strip().lower()
        if u.username.lower() == clean_actor:
            raise ValueError("You cannot delete your own account")

        if u.role == ROLE_ENDMINISTRATOR:
            admin_count_res = await sess.execute(
                select(func.count(UserRow.id)).where(UserRow.role == ROLE_ENDMINISTRATOR)
            )
            if (admin_count_res.scalar() or 0) <= 1:
                raise ValueError("Cannot delete the only remaining Endministrator")

        await sess.delete(u)
        return True


# ─── API Key Sharing CRUD ────────────────────────────────────────────────────

def mask_api_key(key: str) -> str:
    """Mask an API key for safe display (e.g. gsk_...4a2b)."""
    if not key:
        return ""
    if len(key) <= 8:
        return "••••••••"
    return f"{key[:4]}••••••••{key[-4:]}"


async def create_shared_api_key_async(
    owner_username: str,
    key_name: str,
    provider: str,
    api_key: str,
    shared_with: list[str],
) -> dict:
    """Register an API key to share with other users in the application."""
    if not key_name or not key_name.strip():
        raise ValueError("Key name is required")
    if not provider or not provider.strip():
        raise ValueError("Provider is required")
    if not api_key or not api_key.strip():
        raise ValueError("API key value cannot be empty")

    clean_provider = provider.strip().lower()
    clean_shared = [s.strip().lower() for s in shared_with if s and s.strip()]
    if "*" in clean_shared:
        clean_shared = ["*"]

    now = datetime.now(timezone.utc).isoformat()
    key_row = SharedApiKeyRow(
        id=str(uuid.uuid4()),
        owner_username=owner_username.strip().lower(),
        key_name=key_name.strip(),
        provider=clean_provider,
        api_key=api_key.strip(),
        shared_with=j_dumps(clean_shared),
        created_at=now,
        updated_at=now,
    )
    async with _session() as sess:
        sess.add(key_row)
        await sess.flush()
        return {
            "id": key_row.id,
            "owner_username": key_row.owner_username,
            "key_name": key_row.key_name,
            "provider": key_row.provider,
            "api_key": mask_api_key(key_row.api_key),
            "shared_with": clean_shared,
            "created_at": key_row.created_at,
            "updated_at": key_row.updated_at,
        }


async def list_shared_keys_for_user_async(username: str) -> dict:
    """
    List keys owned by this user, and keys shared with this user by others.
    Also returns a list of available application users to share with.
    """
    clean_user = username.strip().lower()
    async with _session() as sess:
        # 1. All users for recipient selection
        u_res = await sess.execute(select(UserRow.username, UserRow.display_name).order_by(UserRow.username.asc()))
        available_users = [
            {"username": r[0], "display_name": r[1] or r[0]}
            for r in u_res.fetchall()
            if r[0] != clean_user
        ]

        # 2. All keys
        k_res = await sess.execute(select(SharedApiKeyRow).order_by(SharedApiKeyRow.created_at.desc()))
        all_keys = k_res.scalars().all()

        my_keys = []
        shared_with_me = []

        for k in all_keys:
            recipients = j_loads(k.shared_with) or []
            if k.owner_username == clean_user:
                my_keys.append({
                    "id": k.id,
                    "owner_username": k.owner_username,
                    "key_name": k.key_name,
                    "provider": k.provider,
                    "api_key": mask_api_key(k.api_key),
                    "shared_with": recipients,
                    "created_at": k.created_at,
                    "updated_at": k.updated_at,
                })
            else:
                # Check if shared with this user or with all ("*")
                if "*" in recipients or clean_user in recipients:
                    shared_with_me.append({
                        "id": k.id,
                        "owner_username": k.owner_username,
                        "key_name": k.key_name,
                        "provider": k.provider,
                        "api_key": mask_api_key(k.api_key),
                        "shared_with": recipients,
                        "created_at": k.created_at,
                    })

        return {
            "my_keys": my_keys,
            "shared_with_me": shared_with_me,
            "available_users": available_users,
        }


async def update_shared_api_key_async(
    key_id: str,
    actor_username: str,
    is_admin: bool,
    key_name: Optional[str] = None,
    api_key: Optional[str] = None,
    shared_with: Optional[list[str]] = None,
) -> dict:
    """Update a shared API key. Requires ownership or Endministrator role."""
    clean_actor = actor_username.strip().lower()
    async with _session() as sess:
        k = await sess.get(SharedApiKeyRow, key_id)
        if not k:
            raise ValueError("Shared key not found")
        if k.owner_username != clean_actor and not is_admin:
            raise ValueError("Permission denied: You do not own this shared key")

        if key_name and key_name.strip():
            k.key_name = key_name.strip()
        if api_key and api_key.strip():
            k.api_key = api_key.strip()
        if shared_with is not None:
            clean_shared = [s.strip().lower() for s in shared_with if s and s.strip()]
            if "*" in clean_shared:
                clean_shared = ["*"]
            k.shared_with = j_dumps(clean_shared)

        k.updated_at = datetime.now(timezone.utc).isoformat()
        await sess.flush()
        return {
            "id": k.id,
            "owner_username": k.owner_username,
            "key_name": k.key_name,
            "provider": k.provider,
            "api_key": mask_api_key(k.api_key),
            "shared_with": j_loads(k.shared_with) or [],
            "created_at": k.created_at,
            "updated_at": k.updated_at,
        }


async def delete_shared_api_key_async(key_id: str, actor_username: str, is_admin: bool) -> bool:
    """Delete a shared key. Requires ownership or Endministrator role."""
    clean_actor = actor_username.strip().lower()
    async with _session() as sess:
        k = await sess.get(SharedApiKeyRow, key_id)
        if not k:
            raise ValueError("Shared key not found")
        if k.owner_username != clean_actor and not is_admin:
            raise ValueError("Permission denied: You do not own this shared key")

        await sess.delete(k)
        return True


async def use_shared_api_key_async(key_id: str, username: str) -> dict:
    """
    Apply a shared key directly to the active provider settings for the current user.
    """
    clean_user = username.strip().lower()
    async with _session() as sess:
        k = await sess.get(SharedApiKeyRow, key_id)
        if not k:
            raise ValueError("Shared key not found")

        recipients = j_loads(k.shared_with) or []
        if k.owner_username != clean_user and "*" not in recipients and clean_user not in recipients:
            raise ValueError("Permission denied: This key has not been shared with you")

        u = await get_user_by_username_async(clean_user)
        user_id = u["id"] if u else None

        from app.storage import load_user_prefs_async, save_user_prefs_async
        prefs = await load_user_prefs_async(user_id)
        provider = k.provider.lower()

        # Apply key to matching setting and record source
        source_label = f"shared:{k.owner_username}:{k.key_name}:{k.id}"
        if provider == "groq":
            prefs["groq_api_key"] = k.api_key
            prefs["groq_key_source"] = source_label
        elif provider == "google":
            prefs["google_api_key"] = k.api_key
            prefs["google_key_source"] = source_label
        elif provider == "nvidia":
            prefs["nvidia_api_key"] = k.api_key
            prefs["nvidia_key_source"] = source_label
        elif provider == "openai":
            prefs["image_gen_api_key"] = k.api_key
            prefs["image_gen_key_source"] = source_label
        elif provider == "tavily":
            prefs["tavily_api_key"] = k.api_key
            prefs["tavily_key_source"] = source_label
        elif provider == "custom":
            prefs["custom_api_key"] = k.api_key
            prefs["custom_key_source"] = source_label
        else:
            prefs[f"{provider}_api_key"] = k.api_key
            prefs[f"{provider}_key_source"] = source_label

        await save_user_prefs_async(user_id, prefs)
        return {
            "ok": True,
            "key_name": k.key_name,
            "provider": k.provider,
            "message": f"Successfully activated shared key '{k.key_name}' for {k.provider.title()}",
        }
