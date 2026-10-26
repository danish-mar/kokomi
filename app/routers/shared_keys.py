from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import (
    get_current_user,
    create_shared_api_key_async,
    list_shared_keys_for_user_async,
    update_shared_api_key_async,
    delete_shared_api_key_async,
    use_shared_api_key_async,
    ROLE_ENDMINISTRATOR,
)

router = APIRouter(prefix="/api/shared-keys", tags=["shared_keys"])


class CreateSharedKeyRequest(BaseModel):
    key_name: str = Field(..., min_length=1, max_length=100)
    provider: str = Field(..., min_length=1, max_length=50) # groq, google, nvidia, openai, tavily, custom
    api_key: str = Field(..., min_length=1)
    shared_with: List[str] = Field(default=["*"]) # ["*"] for all, or list of usernames


class UpdateSharedKeyRequest(BaseModel):
    key_name: Optional[str] = None
    api_key: Optional[str] = None
    shared_with: Optional[List[str]] = None


@router.get("")
async def get_shared_keys(current_user: dict = Depends(get_current_user)):
    """List keys owned by current user and keys shared with current user."""
    res = await list_shared_keys_for_user_async(current_user["username"])
    return {"ok": True, **res}


@router.post("")
async def create_shared_key(
    req: CreateSharedKeyRequest,
    current_user: dict = Depends(get_current_user),
):
    """Register and share an API key with other users in the application."""
    try:
        key = await create_shared_api_key_async(
            owner_username=current_user["username"],
            key_name=req.key_name,
            provider=req.provider,
            api_key=req.api_key,
            shared_with=req.shared_with,
        )
        return {"ok": True, "key": key}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.put("/{key_id}")
async def update_shared_key(
    key_id: str,
    req: UpdateSharedKeyRequest,
    current_user: dict = Depends(get_current_user),
):
    """Update a shared key. Requires ownership or Endministrator role."""
    is_admin = current_user.get("role") == ROLE_ENDMINISTRATOR
    try:
        updated = await update_shared_api_key_async(
            key_id=key_id,
            actor_username=current_user["username"],
            is_admin=is_admin,
            key_name=req.key_name,
            api_key=req.api_key,
            shared_with=req.shared_with,
        )
        return {"ok": True, "key": updated}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.delete("/{key_id}")
async def delete_shared_key(
    key_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Delete a shared key. Requires ownership or Endministrator role."""
    is_admin = current_user.get("role") == ROLE_ENDMINISTRATOR
    try:
        await delete_shared_api_key_async(
            key_id=key_id,
            actor_username=current_user["username"],
            is_admin=is_admin,
        )
        return {"ok": True, "deleted": key_id}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.post("/{key_id}/use")
async def use_shared_key(
    key_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Activate a shared key for the current user's provider settings."""
    try:
        result = await use_shared_api_key_async(key_id=key_id, username=current_user["username"])
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
