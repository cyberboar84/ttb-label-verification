"""Government health warning verification.

This is the highest-stakes check in the app. Per Jenny: the warning must be
exact, word-for-word, and "GOVERNMENT WARNING:" must be in all caps. People
try to get creative (smaller font, title case, reworded text); all of those are
rejections.

Design note: we check the warning against the VERBATIM OCR text, never the
VLM's reading of it. A vision LLM will silently "correct" a mangled warning back
to the canonical text, which would hide exactly the violation we need to catch.
OCR preserves the actual characters and casing on the label.
"""

from __future__ import annotations

import re

from .models import FieldResult, Verdict

# 27 CFR 16.21, the mandatory statement, verbatim.
CANONICAL_WARNING = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not "
    "drink alcoholic beverages during pregnancy because of the risk of birth "
    "defects. (2) Consumption of alcoholic beverages impairs your ability to "
    "drive a car or operate machinery, and may cause health problems."
)

# The literal prefix that regulation requires in capital letters.
REQUIRED_PREFIX = "GOVERNMENT WARNING:"

# The statutory warning's distinct clauses. We verify these are present (fuzzily)
# rather than demanding a character-perfect match, real-photo OCR is never
# perfect, but the *content* (clauses) is what regulation requires. Missing a
# clause is a real violation; garbled characters within a clause are not.
_KEY_CLAUSES = [
    "according to the surgeon general",
    "women should not drink alcoholic beverages during pregnancy",
    "because of the risk of birth defects",
    "impairs your ability to drive a car or operate machinery",
    "may cause health problems",
]
_CLAUSE_THRESHOLD = 78  # rapidfuzz partial_ratio; tolerant of OCR noise


def _normalize_for_wording(text: str) -> str:
    """Collapse whitespace and lowercase for content comparison. Casing is
    checked separately so we can distinguish a wording error from a caps error."""
    text = text.replace("’", "'").replace("‘", "'")  # smart quotes
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def extract_warning_span(raw_ocr: str) -> str | None:
    """Locate the warning within the full OCR text, starting at the
    'government warning' anchor (case-insensitive). Returns the verbatim span."""
    m = re.search(r"government\s+warning", raw_ocr, flags=re.IGNORECASE)
    if not m:
        return None
    return raw_ocr[m.start():].strip()


def check_warning(raw_ocr: str | None) -> FieldResult:
    """Verify the government warning from verbatim OCR text.

    Returns a FieldResult with verdict:
      - MISSING:  no warning found at all
      - MISMATCH: wording differs from statute, OR the required prefix is not
                  in all caps (Jenny's title-case rejection)
      - REVIEW:   wording is very close but not exact (likely OCR noise vs. a
                  real violation, a human should glance)
      - PASS:     prefix is all-caps and wording matches the statute
    """
    field = "government_warning"
    expected_preview = CANONICAL_WARNING

    if not raw_ocr or not raw_ocr.strip():
        return FieldResult(
            field=field, verdict=Verdict.MISSING, expected=expected_preview,
            found=None, note="No text could be read from the label.",
        )

    span = extract_warning_span(raw_ocr)
    if span is None:
        return FieldResult(
            field=field, verdict=Verdict.MISSING, expected=expected_preview,
            found=None, note="No government warning statement found on the label.",
        )

    # 1) Caps check on the required prefix. We look at the raw label characters.
    #    If "GOVERNMENT WARNING" appears but NOT in all caps, that's a violation
    #    even if the wording is perfect.
    has_allcaps_prefix = "GOVERNMENT WARNING" in raw_ocr
    prefix_present_anycase = re.search(
        r"government\s+warning", raw_ocr, flags=re.IGNORECASE
    ) is not None

    from rapidfuzz import fuzz

    norm_found = _normalize_for_wording(span)
    norm_canon = _normalize_for_wording(CANONICAL_WARNING)

    # 2) Caps violation (Jenny's title-case catch), independent of wording.
    if not has_allcaps_prefix and prefix_present_anycase:
        return FieldResult(
            field=field, verdict=Verdict.MISMATCH, expected=expected_preview,
            found=span[:200],
            note="'GOVERNMENT WARNING:' is not in all capital letters as required "
                 "(27 CFR 16.21).",
        )

    # 3) Fast path: clean label scans match the statute (near-)exactly.
    if (norm_found.startswith(norm_canon)
            or fuzz.ratio(norm_found[: len(norm_canon)], norm_canon) >= 97):
        return FieldResult(
            field=field, verdict=Verdict.PASS, expected=expected_preview,
            found=span[:200], score=100.0,
            note="Present, exact statutory wording, in all capital letters.",
        )

    # 4) Robust path (judgment, not character-matching): require each statutory
    #    CLAUSE to appear, fuzzily. OCR noise within a clause is tolerated, but a
    #    reworded or missing clause still fails. A warning that is present yet
    #    only partially readable goes to human review, not an auto-reject, 
    #    which mirrors how an agent handles a poorly-shot image.
    present = sum(1 for c in _KEY_CLAUSES
                  if fuzz.partial_ratio(c, norm_found) >= _CLAUSE_THRESHOLD)
    total = len(_KEY_CLAUSES)
    if present == total:
        return FieldResult(
            field=field, verdict=Verdict.PASS, expected=expected_preview,
            found=span[:200], score=100.0,
            note="All required statutory clauses present, in all capital letters.",
        )
    if present >= 1:
        return FieldResult(
            field=field, verdict=Verdict.REVIEW, expected=expected_preview,
            found=span[:200], score=float(round(100 * present / total)),
            note=f"Warning detected, but only {present} of {total} required clauses "
                 "could be read clearly (likely image quality), please review.",
        )
    return FieldResult(
        field=field, verdict=Verdict.MISMATCH, expected=expected_preview,
        found=span[:200], score=0.0,
        note="No recognizable statutory warning content, the warning appears "
             "reworded or absent.",
    )
