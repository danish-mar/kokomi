import os
import shutil
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from pydantic import BaseModel, Field

from app.auth import (
    require_endministrator,
    list_users_async,
    create_user_async,
    update_user_async,
    delete_user_async,
    VALID_ROLES,
    ROLE_STANDARD,
)
from app.config import AVATARS_DIR

router = APIRouter(prefix="/api/admin/users", tags=["admin_users"])


class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=50)
    password: str = Field(..., min_length=4)
    role: str = Field(default=ROLE_STANDARD)
    display_name: Optional[str] = None
    avatar: Optional[str] = None


class UpdateUserRequest(BaseModel):
    password: Optional[str] = None
    role: Optional[str] = None
    display_name: Optional[str] = None
    avatar: Optional[str] = None


@router.get("")
async def get_all_users(current_admin: dict = Depends(require_endministrator)):
    """List all registered users (Endministrator only)."""
    users = await list_users_async()
    return {"ok": True, "users": users}


@router.post("")
async def create_user(req: CreateUserRequest, current_admin: dict = Depends(require_endministrator)):
    """Create a new user with either Endministrator or Standard user role."""
    try:
        user = await create_user_async(
            username=req.username,
            password=req.password,
            role=req.role,
            display_name=req.display_name,
            avatar=req.avatar,
        )
        return {"ok": True, "user": user}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.put("/{user_id}")
async def update_user(
    user_id: str,
    req: UpdateUserRequest,
    current_admin: dict = Depends(require_endministrator),
):
    """Update a user's details, password, role, or avatar."""
    try:
        user = await update_user_async(
            user_id=user_id,
            actor_username=current_admin["username"],
            password=req.password,
            role=req.role,
            display_name=req.display_name,
            avatar=req.avatar,
        )
        return {"ok": True, "user": user}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.post("/{user_id}/avatar")
async def upload_user_avatar_admin(
    user_id: str,
    avatar: UploadFile = File(...),
    current_admin: dict = Depends(require_endministrator),
):
    """Upload a profile picture for a specific user."""
    try:
        os.makedirs(AVATARS_DIR, exist_ok=True)
        ext = os.path.splitext(avatar.filename or "")[1] or ".png"
        fname = f"user_{user_id[:8]}_{int(datetime.now().timestamp())}{ext}"
        target_path = os.path.join(AVATARS_DIR, fname)
        with open(target_path, "wb") as f:
            shutil.copyfileobj(avatar.file, f)

        avatar_url = f"/avatars/{fname}"
        user = await update_user_async(
            user_id=user_id,
            actor_username=current_admin["username"],
            avatar=avatar_url,
        )
        return {"ok": True, "avatar": avatar_url, "user": user}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.delete("/{user_id}")
async def delete_user(
    user_id: str,
    current_admin: dict = Depends(require_endministrator),
):
    """Delete a user. Cannot delete self or last remaining Endministrator."""
    try:
        await delete_user_async(user_id=user_id, actor_username=current_admin["username"])
        return {"ok": True, "deleted": user_id}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
