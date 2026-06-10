"""Field matching engine.

Each field has a matching strategy chosen to fit how that field actually varies
on real labels (straight from the stakeholder interviews):

  - brand_name    -> fuzzy (Dave's "STONE'S THROW" vs "Stone's Throw")
  - class_type    -> fuzzy, lenient (designations get abbreviated/reordered)
  - alcohol_content -> numeric, parse the % and compare with tolerance
  - net_contents  -> numeric + unit normalization (mL / L / fl oz)

Thresholds are intentionally explicit and tunable. The REVIEW band exists so the
app augments agents rather than silently auto-rejecting, Dave keeps his judgment.
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz

from .models import (
    ApplicationData,
    ExtractedFields,
    FieldResult,
    Verdict,
    VerificationResult,
)
from .textfold import fold_numeric

# Fuzzy thresholds (0-100).
BRAND_PASS = 90.0
BRAND_REVIEW = 75.0
CLASS_PASS = 85.0
CLASS_REVIEW = 65.0

# ABV tolerance in percentage points. TTB allows a labeling tolerance, but for a
# form-vs-label *consistency* check we want the printed number to match what was
# applied for. Small epsilon absorbs "45" vs "45.0".
ABV_TOLERANCE = 0.1


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _fuzzy(a: str, b: str) -> float:
    """Case-insensitive, punctuation-tolerant similarity."""
    return float(fuzz.WRatio(a.casefold(), b.casefold()))


def _missing(field: str, expected: str | None, found: str | None) -> FieldResult | None:
    """Shared MISSING handling. Returns a FieldResult if either side is empty,
    else None (meaning: proceed to real comparison)."""
    exp, fnd = _clean(expected), _clean(found)
    if not exp and not fnd:
        return None
    if not fnd:
        return FieldResult(field=field, verdict=Verdict.MISSING, expected=exp or None,
                           found=None, note="Not found on the label.")
    if not exp:
        return FieldResult(field=field, verdict=Verdict.REVIEW, expected=None,
                           found=fnd, note="Present on label but no application "
                                           "value to compare against.")
    return None


def match_text(field: str, expected: str | None, found: str | None,
               pass_t: float, review_t: float) -> FieldResult:
    pre = _missing(field, expected, found)
    if pre:
        return pre
    exp, fnd = _clean(expected), _clean(found)
    score = _fuzzy(exp, fnd)
    if score >= pass_t:
        verdict = Verdict.PASS
        note = "Match." if score == 100 else f"Close match ({score:.0f}%)."
    elif score >= review_t:
        verdict = Verdict.REVIEW
        note = f"Possible match ({score:.0f}%), verify; may be a formatting variant."
    else:
        verdict = Verdict.MISMATCH
        note = f"Does not match application ({score:.0f}%)."
    return FieldResult(field=field, verdict=verdict, expected=exp, found=fnd,
                       score=round(score, 1), note=note)


def _parse_abv(text: str | None) -> float | None:
    """Pull the alcohol-by-volume percentage from free text.
    Handles '45% Alc./Vol. (90 Proof)', 'ALC 45% BY VOL', '45.5%', etc.
    Falls back to proof/2 if only proof is present."""
    if not text:
        return None
    text = fold_numeric(text)  # "4O%" -> "40%" (numeric-homoglyph evasion)
    m = re.search(r"(\d{1,2}(?:\.\d+)?)\s*%", text)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d{1,3}(?:\.\d+)?)\s*proof", text, flags=re.IGNORECASE)
    if m:
        return float(m.group(1)) / 2.0
    return None


def match_abv(expected: str | None, found: str | None) -> FieldResult:
    field = "alcohol_content"
    pre = _missing(field, expected, found)
    if pre:
        return pre
    exp_v, fnd_v = _parse_abv(expected), _parse_abv(found)
    exp, fnd = _clean(expected), _clean(found)
    if exp_v is None or fnd_v is None:
        # Could not parse a number on one side, fall back to text comparison.
        return match_text(field, expected, found, pass_t=90, review_t=75)
    delta = abs(exp_v - fnd_v)
    if delta <= ABV_TOLERANCE:
        return FieldResult(field=field, verdict=Verdict.PASS, expected=exp, found=fnd,
                           score=100.0, note=f"ABV matches ({fnd_v:g}%).")
    return FieldResult(field=field, verdict=Verdict.MISMATCH, expected=exp, found=fnd,
                       note=f"ABV differs: application {exp_v:g}% vs label {fnd_v:g}%.")


# Net-contents unit normalization, everything to milliliters.
_UNIT_TO_ML = {
    "ml": 1.0, "milliliter": 1.0, "milliliters": 1.0, "millilitre": 1.0,
    "l": 1000.0, "liter": 1000.0, "liters": 1000.0, "litre": 1000.0,
    "cl": 10.0, "centiliter": 10.0,
    "floz": 29.5735, "fl oz": 29.5735, "fluidounce": 29.5735, "oz": 29.5735,
}


def _parse_volume_ml(text: str | None) -> float | None:
    if not text:
        return None
    t = fold_numeric(text).lower().replace("fl. oz", "floz").replace("fl oz", "floz")
    t = t.replace("fl.oz", "floz")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(ml|milliliters?|millilitres?|l|liters?|"
                  r"litres?|cl|centiliters?|floz|oz)\b", t)
    if not m:
        return None
    value, unit = float(m.group(1)), m.group(2)
    factor = _UNIT_TO_ML.get(unit)
    return value * factor if factor else None


def match_net_contents(expected: str | None, found: str | None) -> FieldResult:
    field = "net_contents"
    pre = _missing(field, expected, found)
    if pre:
        return pre
    exp_ml, fnd_ml = _parse_volume_ml(expected), _parse_volume_ml(found)
    exp, fnd = _clean(expected), _clean(found)
    if exp_ml is None or fnd_ml is None:
        return match_text(field, expected, found, pass_t=90, review_t=75)
    # 0.5% relative tolerance for rounding (e.g. 750 mL vs 25.4 fl oz).
    if abs(exp_ml - fnd_ml) <= max(0.005 * exp_ml, 0.5):
        return FieldResult(field=field, verdict=Verdict.PASS, expected=exp, found=fnd,
                           score=100.0, note=f"Net contents match ({fnd_ml:g} mL).")
    return FieldResult(field=field, verdict=Verdict.MISMATCH, expected=exp, found=fnd,
                       note=f"Net contents differ: application {exp_ml:g} mL "
                            f"vs label {fnd_ml:g} mL.")


def _rollup(results: list[FieldResult]) -> Verdict:
    """Overall verdict = worst field outcome. Order of severity:
    MISMATCH > MISSING > REVIEW > PASS."""
    order = [Verdict.MISMATCH, Verdict.MISSING, Verdict.REVIEW, Verdict.PASS]
    present = {r.verdict for r in results}
    for v in order:
        if v in present:
            return v
    return Verdict.PASS


def _presence(key: str, value: str | None, required: bool = True,
              optional_note: str = "") -> FieldResult:
    """Presence check for one mandatory element. Not-required + absent is a PASS
    (so it never drags the overall verdict down) with an explanatory note."""
    val = (value or "").strip()
    if val:
        return FieldResult(field=key, verdict=Verdict.PASS, found=val,
                           note="Present on label.")
    if required:
        return FieldResult(field=key, verdict=Verdict.MISSING, found=None,
                           note="Required element not found on the label.")
    return FieldResult(field=key, verdict=Verdict.PASS, found=None,
                       note=optional_note or "Not required for this beverage type.")


def compliance_only(found: ExtractedFields, warning_result: FieldResult,
                    label_id: str | None = None) -> VerificationResult:
    """Zero-input 'scan the label' check, no application data required.

    Verifies the government warning (exact) and that every mandatory element is
    present, applying TTB's beverage-type-specific rules: alcohol content is
    required for distilled spirits and wine but optional for malt beverages, and
    a country-of-origin statement is required only for imports.
    """
    bt = found.beverage_type or "other"
    fields: list[FieldResult] = [
        _presence("brand_name", found.brand_name),
        _presence("class_type", found.class_type),
        _presence("alcohol_content", found.alcohol_content,
                  required=(bt != "malt_beverage"),
                  optional_note="Alcohol content is optional for malt beverages."),
        _presence("net_contents", found.net_contents),
        _presence("name_address", found.name_address),
        _presence("country_of_origin", found.country_of_origin,
                  required=bool(found.imported),
                  optional_note="Country of origin is required only for imports."),
    ]
    overall = _rollup(fields + [warning_result])
    return VerificationResult(label_id=label_id, overall=overall, mode="compliance",
                              fields=fields, warning=warning_result)


def verify(app: ApplicationData, found: ExtractedFields,
           warning_result: FieldResult, label_id: str | None = None) -> VerificationResult:
    """Run the full matching pipeline and assemble the report."""
    fields = [
        match_text("brand_name", app.brand_name, found.brand_name,
                   BRAND_PASS, BRAND_REVIEW),
        match_text("class_type", app.class_type, found.class_type,
                   CLASS_PASS, CLASS_REVIEW),
        match_abv(app.alcohol_content, found.alcohol_content),
        match_net_contents(app.net_contents, found.net_contents),
    ]
    overall = _rollup(fields + [warning_result])
    return VerificationResult(
        label_id=label_id, overall=overall, mode="match",
        fields=fields, warning=warning_result,
    )
