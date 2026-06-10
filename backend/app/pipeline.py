"""Orchestrates verification of one bottle end-to-end.

A real bottle carries its mandatory information across multiple panels: brand and
class are usually on the front, while the government warning, net contents, and
bottler name/address are typically on the back. So a single image is almost never
compliant on its own. This pipeline accepts one *or more* panels (front / back /
side), processes each, then judges the bottle as a whole:

  per panel:  preprocess -> [OCR ‖ VLM extraction]   (concurrent, latency-bound)
  merge:      union the OCR text + take each field from whichever panel has it
  judge:      warning check + injection scan + compliance/matching on the merge
"""

from __future__ import annotations

import asyncio
import time

from .extract import ContentFiltered, extract_fields
from .injection import detect_injection
from .matching import compliance_only, verify
from .models import ApplicationData, ExtractedFields, VerificationResult, Verdict
from .preprocess import preprocess
from .vision import read_text
from .warning import check_warning, extract_warning_span

# Fields merged by "first panel that has a value wins".
_TEXT_FIELDS = ("brand_name", "class_type", "alcohol_content", "net_contents",
                "name_address", "country_of_origin")


async def _process_panel(image_bytes: bytes) -> tuple[str, ExtractedFields, bool]:
    """Process one panel: OCR first (fast, ~0.3s, and reads fine print the VLM
    misses), then VLM field-assignment grounded on that OCR text at low image
    detail. This is both faster and more accurate than parallel high-detail
    vision, because the model assigns fields from text the OCR already captured.

    Returns (ocr_text, fields, content_filtered). If Azure's Prompt Shields blocks
    the call (a real jailbreak in the label), we keep the OCR, return empty
    fields, and signal it so the bottle is flagged for review."""
    clean = preprocess(image_bytes)
    ocr_text = await read_text(clean)
    try:
        fields = await extract_fields(clean, ocr_hint=ocr_text or "")
        return (ocr_text or ""), fields, False
    except ContentFiltered:
        return (ocr_text or ""), ExtractedFields(), True


def _merge_panels(panels: list[ExtractedFields]) -> ExtractedFields:
    """Combine per-panel extractions into one bottle-level record. For each text
    field, take the first non-empty value across panels; classify the beverage
    from any panel that identified it; treat the bottle as imported if any panel
    indicates it."""
    def first(attr: str) -> str | None:
        for p in panels:
            v = getattr(p, attr)
            if v and v.strip():
                return v
        return None

    beverage = next((p.beverage_type for p in panels
                     if p.beverage_type and p.beverage_type != "other"), "other")
    return ExtractedFields(
        **{f: first(f) for f in _TEXT_FIELDS},
        beverage_type=beverage,
        imported=any(p.imported for p in panels),
    )


async def verify_bottle(images: list[bytes], app: ApplicationData,
                        label_id: str | None = None) -> VerificationResult:
    """Verify a bottle from one or more panel images."""
    start = time.perf_counter()

    panels = await asyncio.gather(*(_process_panel(b) for b in images))
    ocr_texts = [p[0] for p in panels]
    extracted = [p[1] for p in panels]
    any_filtered = any(p[2] for p in panels)

    combined_ocr = "\n".join(t for t in ocr_texts if t)
    merged = _merge_panels(extracted)
    merged.raw_text = combined_ocr
    merged.warning_text = extract_warning_span(combined_ocr)

    warning_result = check_warning(combined_ocr)

    if app.is_empty():
        result = compliance_only(merged, warning_result, label_id=label_id)
    else:
        result = verify(app, merged, warning_result, label_id=label_id)

    # Anti-prompt-injection: our deterministic detector on the OCR, plus Azure
    # Prompt Shields (which may have blocked the extraction call above).
    flags = detect_injection(combined_ocr)
    if any_filtered:
        flags = flags + ["Azure content filter blocked the label text "
                         "(possible prompt injection / jailbreak)"]
    if flags:
        result.security_flags = flags
        if result.overall == Verdict.PASS:
            result.overall = Verdict.REVIEW

    result.processing_ms = int((time.perf_counter() - start) * 1000)
    return result


async def verify_label(image_bytes: bytes, app: ApplicationData,
                       label_id: str | None = None) -> VerificationResult:
    """Single-panel convenience wrapper (used by the batch path)."""
    return await verify_bottle([image_bytes], app, label_id=label_id)
