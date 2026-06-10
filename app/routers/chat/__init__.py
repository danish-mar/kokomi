"""Chat & workflow API surface.

Previously a single ~2,000-line `chat.py`; split into focused submodules.
The sub-routers are included in the same order their routes were originally
defined so URL-matching precedence is unchanged.

`app/__init__.py` imports this package as `chat` and registers `chat.router`.
"""
from fastapi import APIRouter

from . import uploads, conversation, workflows, files, templates, sockets, schedules

router = APIRouter()
router.include_router(uploads.router)
router.include_router(conversation.router)
router.include_router(workflows.router)
router.include_router(files.router)
router.include_router(templates.router)
router.include_router(sockets.router)
router.include_router(schedules.router)
