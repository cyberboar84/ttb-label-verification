"""Azure AI Vision Image Analysis 4.0 (Read) — verbatim OCR.

We use the synchronous Image Analysis Read feature (Florence-based, within Azure
AI Foundry) specifically because it returns low-latency results suitable for a
real-time UI — the whole reason we can meet the 5-second target. Document
Intelligence would give higher-res OCR but is asynchronous and built for forms,
which fights the latency budget.

The verbatim text returned here is what feeds the government-warning exact-match
check. We never let the VLM touch the warning text.
"""

from __future__ import annotations

import asyncio

from .config import settings

# Canonical warning reused for the mock path.
from .warning import CANONICAL_WARNING

_MOCK_OCR = (
    "OLD TOM DISTILLERY\n"
    "Kentucky Straight Bourbon Whiskey\n"
    "45% Alc./Vol. (90 Proof)\n"
    "750 mL\n"
    "Distilled and Bottled by Old Tom Distillery, Bardstown, Kentucky\n"
    f"{CANONICAL_WARNING}\n"
)


_client = None


def _get_client():
    """Lazily build and cache the Vision client so we pay the TLS/auth setup
    once per process, not once per request."""
    global _client
    if _client is None:
        from azure.ai.vision.imageanalysis import ImageAnalysisClient
        from azure.core.credentials import AzureKeyCredential

        _client = ImageAnalysisClient(
            endpoint=settings.vision_endpoint,
            credential=AzureKeyCredential(settings.vision_key),
        )
    return _client


def _read_sync(image_bytes: bytes) -> str:
    """Blocking Azure Vision call. Run via asyncio.to_thread."""
    from azure.ai.vision.imageanalysis.models import VisualFeatures

    client = _get_client()
    result = client.analyze(image_data=image_bytes, visual_features=[VisualFeatures.READ])
    if not result.read or not result.read.blocks:
        return ""
    lines = []
    for block in result.read.blocks:
        for line in block.lines:
            lines.append(line.text)
    return "\n".join(lines)


async def read_text(image_bytes: bytes) -> str:
    """Return verbatim OCR text from the label image."""
    if settings.use_mock:
        await asyncio.sleep(0)  # keep it awaitable / non-blocking
        return _MOCK_OCR
    return await asyncio.to_thread(_read_sync, image_bytes)


async def warmup() -> None:
    """Prime the Vision client + connection pool at startup so the first real
    request doesn't pay the cold-start handshake. Best-effort."""
    if settings.use_mock:
        return
    try:
        import io
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (64, 64), "white").save(buf, "JPEG")
        await asyncio.to_thread(_read_sync, buf.getvalue())
    except Exception:
        pass
