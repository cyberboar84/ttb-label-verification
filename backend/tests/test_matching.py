"""Unit tests for the deterministic matching core — the graded intelligence.
These run with zero Azure dependency."""

from app.matching import (
    compliance_only,
    match_abv,
    match_net_contents,
    match_text,
    verify,
    BRAND_PASS,
    BRAND_REVIEW,
)
from app.models import ApplicationData, ExtractedFields, Verdict
from app.warning import CANONICAL_WARNING, REQUIRED_PREFIX, check_warning


# --- Brand name (fuzzy) -----------------------------------------------------

def test_brand_exact():
    r = match_text("brand_name", "Old Tom Distillery", "OLD TOM DISTILLERY",
                   BRAND_PASS, BRAND_REVIEW)
    assert r.verdict == Verdict.PASS


def test_brand_daves_apostrophe_case():
    # Dave's real example: label says STONE'S THROW, application says Stone's Throw.
    r = match_text("brand_name", "Stone's Throw", "STONE'S THROW",
                   BRAND_PASS, BRAND_REVIEW)
    assert r.verdict == Verdict.PASS


def test_brand_mismatch():
    r = match_text("brand_name", "Old Tom Distillery", "Black Cat Spirits",
                   BRAND_PASS, BRAND_REVIEW)
    assert r.verdict == Verdict.MISMATCH


def test_brand_missing():
    r = match_text("brand_name", "Old Tom Distillery", None,
                   BRAND_PASS, BRAND_REVIEW)
    assert r.verdict == Verdict.MISSING


# --- ABV (numeric) ----------------------------------------------------------

def test_abv_match_with_proof():
    r = match_abv("45% Alc./Vol.", "45% Alc./Vol. (90 Proof)")
    assert r.verdict == Verdict.PASS


def test_abv_proof_only_on_label():
    # Label shows only proof; we derive ABV = proof/2.
    r = match_abv("45%", "90 Proof")
    assert r.verdict == Verdict.PASS


def test_abv_mismatch():
    r = match_abv("40% Alc./Vol.", "45% Alc./Vol.")
    assert r.verdict == Verdict.MISMATCH


# --- Net contents (unit-normalized) -----------------------------------------

def test_net_contents_match():
    r = match_net_contents("750 mL", "750 mL")
    assert r.verdict == Verdict.PASS


def test_net_contents_unit_conversion():
    # 750 mL == 25.36 fl oz, should still pass under tolerance.
    r = match_net_contents("750 mL", "25.4 fl oz")
    assert r.verdict == Verdict.PASS


def test_net_contents_mismatch():
    r = match_net_contents("750 mL", "1 L")
    assert r.verdict == Verdict.MISMATCH


# --- Government warning (exact + caps) ---------------------------------------

def test_warning_perfect():
    ocr = f"Some label text\n{CANONICAL_WARNING}\n750 mL"
    r = check_warning(ocr)
    assert r.verdict == Verdict.PASS


def test_warning_title_case_rejected():
    # Jenny's catch: "Government Warning" in title case instead of all caps.
    bad = CANONICAL_WARNING.replace(REQUIRED_PREFIX, "Government Warning:")
    r = check_warning(f"label\n{bad}")
    assert r.verdict == Verdict.MISMATCH
    assert "capital" in r.note.lower()


def test_warning_missing():
    r = check_warning("OLD TOM DISTILLERY\nKentucky Straight Bourbon\n750 mL")
    assert r.verdict == Verdict.MISSING


def test_warning_reworded_rejected():
    bad = ("GOVERNMENT WARNING: Drinking is bad for you and you should not do it "
           "while pregnant or driving.")
    r = check_warning(f"label\n{bad}")
    assert r.verdict == Verdict.MISMATCH


def test_warning_ocr_noise_still_passes():
    # Character-level OCR noise within clauses must NOT cause a false violation —
    # the content (clauses) is all there.
    noisy = (CANONICAL_WARNING.replace("birth defects", "birth defecls")
             .replace("Surgeon General", "Surgeon Genera1"))
    r = check_warning(f"label\n{noisy}")
    assert r.verdict == Verdict.PASS


def test_warning_partial_unreadable_is_review():
    # Only the opening clauses survived OCR (rest garbled/cut off) — present but
    # not fully verifiable -> REVIEW (human looks), not auto-reject.
    partial = ("GOVERNMENT WARNING: (1) According to the Surgeon General, women "
               "should not drink alcoholic beverages during pregnancy 8x3#@ xz9")
    r = check_warning(f"label\n{partial}")
    assert r.verdict == Verdict.REVIEW


# --- End-to-end rollup ------------------------------------------------------

def test_verify_all_pass():
    app = ApplicationData(brand_name="Old Tom Distillery",
                          class_type="Kentucky Straight Bourbon Whiskey",
                          alcohol_content="45% Alc./Vol.", net_contents="750 mL")
    found = ExtractedFields(brand_name="OLD TOM DISTILLERY",
                            class_type="Kentucky Straight Bourbon Whiskey",
                            alcohol_content="45% Alc./Vol. (90 Proof)",
                            net_contents="750 mL")
    wr = check_warning(CANONICAL_WARNING)
    result = verify(app, found, wr)
    assert result.overall == Verdict.PASS
    assert not result.needs_human


def _spirits(**over):
    base = dict(brand_name="Old Tom Distillery",
                class_type="Kentucky Straight Bourbon Whiskey",
                alcohol_content="45% Alc./Vol.", net_contents="750 mL",
                name_address="Bottled by Old Tom Distillery, Bardstown, KY",
                beverage_type="distilled_spirits", imported=False)
    base.update(over)
    return ExtractedFields(**base)


def test_compliance_only_pass():
    r = compliance_only(_spirits(), check_warning(CANONICAL_WARNING))
    assert r.mode == "compliance"
    assert r.overall == Verdict.PASS


def test_compliance_only_missing_element():
    r = compliance_only(_spirits(net_contents=None), check_warning(CANONICAL_WARNING))
    assert r.overall == Verdict.MISSING
    assert any(f.field == "net_contents" and f.verdict == Verdict.MISSING
               for f in r.fields)


def test_compliance_name_address_required():
    r = compliance_only(_spirits(name_address=None), check_warning(CANONICAL_WARNING))
    assert r.overall == Verdict.MISSING


def test_compliance_beer_no_abv_passes():
    # Malt beverage with no ABV is still compliant (ABV optional for beer).
    r = compliance_only(
        _spirits(class_type="India Pale Ale", beverage_type="malt_beverage",
                 alcohol_content=None),
        check_warning(CANONICAL_WARNING))
    assert r.overall == Verdict.PASS


def test_compliance_spirits_no_abv_missing():
    r = compliance_only(_spirits(alcohol_content=None), check_warning(CANONICAL_WARNING))
    assert r.overall == Verdict.MISSING


def test_merge_panels_combines_front_and_back():
    # Real-world: front has brand/class/net; back has warning info, bottler, ABV.
    from app.pipeline import _merge_panels
    front = ExtractedFields(brand_name="Hendrick's", class_type="Gin",
                            net_contents="750 ML", beverage_type="distilled_spirits")
    back = ExtractedFields(name_address="William Grant & Sons, Scotland",
                           alcohol_content="44% Alc./Vol.",
                           country_of_origin="Product of Scotland", imported=True)
    m = _merge_panels([front, back])
    assert m.brand_name == "Hendrick's"
    assert m.net_contents == "750 ML"
    assert m.name_address == "William Grant & Sons, Scotland"
    assert m.alcohol_content == "44% Alc./Vol."
    assert m.beverage_type == "distilled_spirits"
    assert m.imported is True


def test_compliance_import_requires_country():
    no_country = compliance_only(_spirits(imported=True, country_of_origin=None),
                                 check_warning(CANONICAL_WARNING))
    assert no_country.overall == Verdict.MISSING
    with_country = compliance_only(
        _spirits(imported=True, country_of_origin="Product of Scotland"),
        check_warning(CANONICAL_WARNING))
    assert with_country.overall == Verdict.PASS


def test_application_is_empty():
    assert ApplicationData().is_empty()
    assert not ApplicationData(brand_name="X").is_empty()


def test_verify_flags_one_bad_field():
    app = ApplicationData(brand_name="Old Tom Distillery",
                          alcohol_content="45% Alc./Vol.", net_contents="750 mL")
    found = ExtractedFields(brand_name="Old Tom Distillery",
                            alcohol_content="40% Alc./Vol.",  # wrong
                            net_contents="750 mL")
    wr = check_warning(CANONICAL_WARNING)
    result = verify(app, found, wr)
    assert result.overall == Verdict.MISMATCH
    assert result.needs_human
