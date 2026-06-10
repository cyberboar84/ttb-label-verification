"""Pydantic models shared across the verification pipeline.

The application record is what the TTB agent (or COLA, in a real integration)
asserts the label *should* say. The extracted record is what we actually read
off the label image. The matching engine compares the two and produces a
per-field verdict.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Verdict(str, Enum):
    """Per-field outcome. REVIEW is the deliberate middle ground that mirrors
    Dave's "obviously the same thing" judgment call — a human glances at it."""

    PASS = "PASS"
    REVIEW = "REVIEW"
    MISMATCH = "MISMATCH"
    MISSING = "MISSING"


class ApplicationData(BaseModel):
    """The expected values, as submitted in the COLA application."""

    brand_name: str | None = None
    class_type: str | None = None
    alcohol_content: str | None = None  # free text, e.g. "45% Alc./Vol."
    net_contents: str | None = None  # free text, e.g. "750 mL"
    # The government warning is fixed by regulation, so it is not part of the
    # application payload — it is checked against the canonical statute text.

    def is_empty(self) -> bool:
        """True when no application values were supplied — triggers the zero-input
        compliance-only flow (the primary 'scan the label' path)."""
        return not any((self.brand_name, self.class_type,
                        self.alcohol_content, self.net_contents))


class ExtractedFields(BaseModel):
    """What we read off the label. Semantic fields come from the VLM; the
    raw_text + warning_text come verbatim from OCR for exact-match fidelity."""

    brand_name: str | None = None
    class_type: str | None = None
    alcohol_content: str | None = None
    net_contents: str | None = None
    name_address: str | None = None  # name & address of bottler/producer/importer
    country_of_origin: str | None = None  # e.g. "Product of Scotland"; required for imports
    beverage_type: str = "other"  # distilled_spirits | wine | malt_beverage | other
    imported: bool = False
    warning_text: str | None = None  # verbatim OCR span for the warning
    raw_text: str | None = None  # full verbatim OCR, for auditing / fallback


class FieldResult(BaseModel):
    field: str
    verdict: Verdict
    expected: str | None = None
    found: str | None = None
    score: float | None = None  # 0-100 similarity where applicable
    note: str = ""


class VerificationResult(BaseModel):
    """The full report for one label."""

    label_id: str | None = None
    overall: Verdict
    mode: str = "match"  # "compliance" (no app data) or "match" (vs application)
    fields: list[FieldResult]
    warning: FieldResult
    # Reasons the label text appears to be attacking the verification system
    # (prompt injection). Non-empty forces human review — never an auto-PASS.
    security_flags: list[str] = []
    processing_ms: int | None = None

    @property
    def needs_human(self) -> bool:
        return self.overall in (Verdict.REVIEW, Verdict.MISMATCH, Verdict.MISSING)
