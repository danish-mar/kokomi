"""On-demand rendering for chat message artifacts that need a real binary
output (currently: PDF). The artifact's markdown content already lives in the
message record (msg.artifacts[].content); this just turns it into bytes when
the user actually clicks View/Download, so nothing is written to disk for
every PDF artifact a conversation produces."""
import asyncio
import io

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

router = APIRouter(prefix="/api/artifacts")


@router.post("/render-pdf")
async def render_pdf_artifact(payload: dict):
    content = (payload.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")

    from app.pdf_render import render_markdown_to_pdf
    buf = io.BytesIO()
    try:
        # render_markdown_to_pdf is synchronous — ReportLab layout is CPU work,
        # and it does a blocking requests.get() per embedded image (up to a
        # 15s timeout, one at a time). Calling it directly here would freeze
        # the single event loop for every user for however long that takes,
        # every time anyone views or downloads a PDF artifact.
        await asyncio.to_thread(render_markdown_to_pdf, content, buf)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {e}")

    return Response(
        content=buf.getvalue(),
        media_type="application/pdf",
        # inline (not attachment) so a new tab renders it in the browser's
        # native PDF viewer instead of forcing a download.
        headers={"Content-Disposition": "inline"},
    )
