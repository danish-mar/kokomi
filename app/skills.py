"""
Skills — reusable instruction packs the model loads on demand.

A skill is a folder under `data/skills/<slug>/` containing a `SKILL.md`: YAML
frontmatter with a `name` and `description`, then a markdown body of
instructions. This is Anthropic's open Agent Skills format, so skills written
for Claude (including the official open-source ones) drop straight in.

The point is *progressive disclosure*. Only each skill's name and one-line
description sit in the system prompt — cheap, and always present. The body is
pulled in only when the model calls `load_skill`, so a dozen installed skills
cost a dozen lines of context rather than a dozen documents.

Frontmatter is parsed without a YAML dependency: the format's frontmatter is a
flat `key: value` block, and hand-rolling that avoids adding PyYAML for two
fields.
"""
import os
import re
from typing import Optional

SKILLS_DIR = os.path.join("data", "skills")

# Guard rails for what gets injected into a prompt / returned to the model.
MAX_DESCRIPTION_CHARS = 300
MAX_BODY_CHARS = 24000


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return slug or "skill"


def _parse_skill_md(raw: str) -> tuple[dict, str]:
    """Split a SKILL.md into (frontmatter dict, body).

    Tolerates a missing frontmatter block — such a file still loads, it just
    falls back to the folder name and an empty description rather than being
    silently dropped.
    """
    meta: dict = {}
    body = raw

    m = re.match(r"^\s*---\s*\n(.*?)\n---\s*\n?(.*)$", raw, re.DOTALL)
    if m:
        block, body = m.group(1), m.group(2)
        for line in block.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, _, value = line.partition(":")
            value = value.strip().strip('"').strip("'")
            meta[key.strip().lower()] = value

    return meta, body.strip()


def load_skills(include_disabled: bool = False) -> list[dict]:
    """Every installed skill, newest-listed last. Cheap enough to call per
    request: it's a directory scan plus a small read per skill, and the number
    of skills is user-scale (tens, not thousands)."""
    if not os.path.isdir(SKILLS_DIR):
        return []

    skills = []
    for slug in sorted(os.listdir(SKILLS_DIR)):
        folder = os.path.join(SKILLS_DIR, slug)
        path = os.path.join(folder, "SKILL.md")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read()
        except Exception as e:
            print(f"[Skills] failed to read {path}: {e}")
            continue

        meta, body = _parse_skill_md(raw)
        enabled = str(meta.get("enabled", "true")).strip().lower() not in ("false", "0", "no")
        if not enabled and not include_disabled:
            continue

        skills.append({
            "slug": slug,
            "name": meta.get("name") or slug,
            "description": (meta.get("description") or "")[:MAX_DESCRIPTION_CHARS],
            "body": body,
            "enabled": enabled,
            "path": path,
        })
    return skills


def get_skill(slug_or_name: str) -> Optional[dict]:
    """Resolve by slug first, then by display name, then loosely — the model
    supplies this string from the prompt listing, so it may not match exactly."""
    if not slug_or_name:
        return None
    q = slug_or_name.strip().lower()
    skills = load_skills(include_disabled=True)
    for s in skills:
        if s["slug"].lower() == q or s["name"].lower() == q:
            return s
    for s in skills:
        if q in s["name"].lower() or q in s["slug"].lower():
            return s
    return None


def skills_prompt_block() -> str:
    """The always-injected catalogue: names + descriptions only, never bodies.

    Returns "" when nothing is installed, so a fresh install pays no prompt
    cost at all.
    """
    skills = load_skills()
    if not skills:
        return ""
    lines = "\n".join(
        f"- {s['name']}" + (f" — {s['description']}" if s["description"] else "")
        for s in skills
    )
    return (
        "[SKILLS AVAILABLE]\n"
        "You have skills — packaged instructions for specific kinds of work. Only their "
        "names and summaries are listed here; the actual instructions are loaded on demand.\n"
        f"{lines}\n"
        "When a request matches one of these, call the `load_skill` tool with its name FIRST "
        "and follow the instructions it returns. Don't guess at a skill's contents from its "
        "summary, and don't load one that isn't relevant — most messages need no skill at all.\n"
        "[/SKILLS AVAILABLE]"
    )


def save_skill(slug: Optional[str], name: str, description: str, body: str,
               enabled: bool = True) -> dict:
    """Create or update a skill, writing it back out as a valid SKILL.md.

    Passing an existing `slug` edits in place (so renaming a skill doesn't
    orphan the old folder); omitting it derives one from the name.
    """
    slug = (slug or _slugify(name)).strip()
    folder = os.path.join(SKILLS_DIR, slug)
    os.makedirs(folder, exist_ok=True)

    # Quotes keep values that contain a colon from breaking the frontmatter on
    # the next read.
    def q(v: str) -> str:
        return '"' + (v or "").replace('"', "'").replace("\n", " ").strip() + '"'

    content = (
        "---\n"
        f"name: {q(name)}\n"
        f"description: {q(description)}\n"
        f"enabled: {'true' if enabled else 'false'}\n"
        "---\n\n"
        f"{(body or '').strip()}\n"
    )
    with open(os.path.join(folder, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(content)
    return {"slug": slug, "name": name, "description": description, "enabled": enabled}


def delete_skill(slug: str) -> bool:
    import shutil
    folder = os.path.join(SKILLS_DIR, (slug or "").strip())
    # Refuse to walk outside the skills directory on a crafted slug.
    if not os.path.isdir(folder) or not os.path.abspath(folder).startswith(os.path.abspath(SKILLS_DIR)):
        return False
    shutil.rmtree(folder)
    return True
