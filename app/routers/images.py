import os
import hashlib
import base64
import httpx
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.config import GENERATED_DIR
from app.storage import load_prefs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/images", tags=["images"])


class ImageGenerationRequest(BaseModel):
    prompt: str = Field(..., description="Prompt for image generation")
    model: Optional[str] = Field(None, description="Model to use for image generation")
    size: Optional[str] = Field("1024x1024", description="Size of image e.g. 1024x1024")
    n: Optional[int] = Field(1, description="Number of images")


class ImageGenerationResponse(BaseModel):
    url: str
    prompt: str
    revised_prompt: Optional[str] = None


async def generate_image_internal(prompt: str, size: str = "1024x1024") -> dict:
    prefs = load_prefs()
    enabled = prefs.get("image_gen_enabled", True)
    if not enabled:
        raise HTTPException(status_code=400, detail="Image generation is disabled in settings.")

    api_key = (prefs.get("image_gen_api_key") or prefs.get("custom_api_key") or "").strip()
    base_url = (prefs.get("image_gen_base_url") or "https://api.openai.com/v1").rstrip("/")
    model = prefs.get("image_gen_model") or "dall-e-3"

    url = f"{base_url}/images/generations"
    headers = {
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    else:
        headers["Authorization"] = "Bearer dummy"

    payload = {
        "prompt": prompt,
        "model": model,
        "n": 1,
        "size": size,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code != 200:
                logger.error(f"Image generation failed: {resp.status_code} - {resp.text}")
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"Image generation API error: {resp.text}"
                )
            data = resp.json()
        except httpx.HTTPError as e:
            logger.error(f"HTTP error during image generation: {e}")
            raise HTTPException(status_code=502, detail=f"Image generation network error: {str(e)}")

    item = data.get("data", [{}])[0]
    revised_prompt = item.get("revised_prompt")
    
    # Save image locally to GENERATED_DIR
    os.makedirs(GENERATED_DIR, exist_ok=True)
    filename = f"{hashlib.md5(prompt.encode('utf-8')).hexdigest()[:12]}_{int(hashlib.sha256(prompt.encode('utf-8')).hexdigest()[:8], 16)}.png"
    filepath = os.path.join(GENERATED_DIR, filename)
    local_url = f"/static/generated/{filename}"

    if "b64_json" in item and item["b64_json"]:
        image_bytes = base64.b64decode(item["b64_json"])
        with open(filepath, "wb") as f:
            f.write(image_bytes)
    elif "url" in item and item["url"]:
        image_remote_url = item["url"]
        async with httpx.AsyncClient(timeout=30.0) as client:
            img_resp = await client.get(image_remote_url)
            if img_resp.status_code == 200:
                with open(filepath, "wb") as f:
                    f.write(img_resp.content)
            else:
                # Fallback to remote URL if download fails
                local_url = image_remote_url
    else:
        raise HTTPException(status_code=500, detail="No image data or URL returned by provider.")

    return {
        "url": local_url,
        "prompt": prompt,
        "revised_prompt": revised_prompt,
    }


@router.post("/generations", response_model=ImageGenerationResponse)
async def generate_image_endpoint(req: ImageGenerationRequest):
    res = await generate_image_internal(prompt=req.prompt, size=req.size or "1024x1024")
    return ImageGenerationResponse(**res)
