"""
Skills router — manage the instruction packs the model loads via `load_skill`.

  GET    /api/skills                list installed skills (bodies included)
  POST   /api/skills                create or update one (in-app editor)
  DELETE /api/skills/{slug}         remove one
  POST   /api/skills/import-github  pull a SKILL.md straight from a repo

Skills live on disk as `data/skills/<slug>/SKILL.md` in Anthropic's Agent
Skills format, so the filesystem stays the single source of truth and skills
written elsewhere can simply be dropped in.
"""
import asyncio
import re

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.skills import load_skills, save_skill, delete_skill, get_skill

router = APIRouter(prefix="/api/skills")

# Raw-content hosts we'll fetch a SKILL.md from. Anything else is rejected
# rather than letting an arbitrary URL be fetched server-side.
_ALLOWED_HOSTS = ("raw.githubusercontent.com", "github.com", "gist.githubusercontent.com")


class SkillPayload(BaseModel):
    slug: str | None = None
    name: str
    description: str = ""
    body: str = ""
    enabled: bool = True


class ImportPayload(BaseModel):
    url: str


@router.get("")
async def list_skills():
    skills = await asyncio.to_thread(load_skills, True)
    return {"skills": skills}


@router.post("")
async def upsert_skill(payload: SkillPayload):
    if not (payload.name or "").strip():
        raise HTTPException(400, "A skill needs a name")
    saved = await asyncio.to_thread(
        save_skill, payload.slug, payload.name, payload.description,
        payload.body, payload.enabled,
    )
    return {"ok": True, "skill": saved}


@router.delete("/{slug}")
async def remove_skill(slug: str):
    ok = await asyncio.to_thread(delete_skill, slug)
    if not ok:
        raise HTTPException(404, "Skill not found")
    return {"ok": True}


def _to_raw_url(url: str) -> str:
    """Accept a normal GitHub file URL as well as a raw one — pasting the page
    URL you were just looking at is the obvious thing to try."""
    url = (url or "").strip()
    m = re.match(r"https://github\.com/([^/]+)/([^/]+)/blob/(.+)$", url)
    if m:
        return f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{m.group(3)}"
    return url


@router.post("/import-github")
async def import_skill(payload: ImportPayload):
    """Fetch a SKILL.md from GitHub and install it locally."""
    url = _to_raw_url(payload.url)
    if not url.startswith("https://") or not any(h in url for h in _ALLOWED_HOSTS):
        raise HTTPException(400, "Only GitHub raw SKILL.md URLs are supported")
    if not url.lower().endswith(".md"):
        raise HTTPException(400, "URL must point directly at a SKILL.md file")

    try:
        resp = await asyncio.to_thread(requests.get, url, timeout=15)
    except Exception as e:
        raise HTTPException(502, f"Couldn't reach GitHub: {e}")
    if resp.status_code != 200:
        raise HTTPException(502, f"GitHub returned {resp.status_code} for that URL")

    from app.skills import _parse_skill_md, _slugify
    meta, body = _parse_skill_md(resp.text)
    name = meta.get("name")
    if not name:
        # Fall back to the containing folder, which in the standard layout is
        # the skill's own directory (…/skills/pdf-filling/SKILL.md).
        parts = [p for p in url.split("/") if p]
        name = parts[-2] if len(parts) > 1 else "imported-skill"
    if not body.strip():
        raise HTTPException(400, "That file has no skill instructions in it")

    saved = await asyncio.to_thread(
        save_skill, _slugify(name), name, meta.get("description", ""), body, True,
    )
    return {"ok": True, "skill": saved}
