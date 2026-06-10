"""Generate synthetic TTB-style test labels with known ground truth.

Each label is rendered with real, OCR-readable text so it can be run through the
live Azure pipeline. The scenarios map directly to the stakeholder interviews:

  old_tom_good          clean, fully compliant label (PASS)
  stones_throw_caps     brand in ALL CAPS vs title-case application (Dave)
  silver_creek_titlecase  'Government Warning' in title case (Jenny — REJECT)
  copper_ridge_no_warn  government warning entirely missing (REJECT)
  harbor_gin_wrong_abv  label ABV differs from application (MISMATCH)
  old_tom_tilted        same as good, rotated + dimmed (preprocessing test)

Writes images + manifest.json (the application data) + expected_results.md.
Run:  python samples/generate_samples.py
"""

from __future__ import annotations

import json
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(__file__)

CANONICAL_WARNING = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not "
    "drink alcoholic beverages during pregnancy because of the risk of birth "
    "defects. (2) Consumption of alcoholic beverages impairs your ability to "
    "drive a car or operate machinery, and may cause health problems."
)

W, H = 800, 1100


def _font(names, size):
    for n in names:
        for base in ("/System/Library/Fonts/Supplemental/", "/Library/Fonts/",
                     "/System/Library/Fonts/"):
            p = base + n
            if os.path.exists(p):
                return ImageFont.truetype(p, size)
    return ImageFont.load_default()


SERIF = lambda s: _font(["Georgia.ttf", "Times New Roman.ttf"], s)
SANS = lambda s: _font(["Arial.ttf", "Helvetica.ttc"], s)


def _wrap(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _center(draw, y, text, font, fill="#1a1a1a"):
    w = draw.textlength(text, font=font)
    draw.text(((W - w) / 2, y), text, font=font, fill=fill)


def render(brand, class_type, abv_text, net, producer, warning,
           bg="#f3ecd8", accent="#5a3a1a"):
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    d.rectangle([20, 20, W - 20, H - 20], outline=accent, width=6)
    d.rectangle([34, 34, W - 34, H - 34], outline=accent, width=2)

    _center(d, 120, brand, SERIF(64), accent)
    d.line([140, 215, W - 140, 215], fill=accent, width=2)
    _center(d, 250, "ESTABLISHED 1921", SANS(20), accent)

    for i, line in enumerate(_wrap(d, class_type, SERIF(34), W - 220)):
        _center(d, 360 + i * 46, line, SERIF(34))

    _center(d, 540, abv_text, SANS(30))
    _center(d, 590, net, SANS(30))
    _center(d, 700, producer, SANS(22), "#444")

    # Government warning block, small print near the bottom.
    if warning:
        wf = SANS(17)
        y = 850
        for line in _wrap(d, warning, wf, W - 130):
            d.text((65, y), line, font=wf, fill="#1a1a1a")
            y += 24
    return img


SCENARIOS = [
    dict(key="old_tom_good",
         brand="Old Tom Distillery", class_type="Kentucky Straight Bourbon Whiskey",
         abv_text="45% Alc./Vol. (90 Proof)", net="750 mL",
         producer="Distilled & Bottled by Old Tom Distillery, Bardstown, KY",
         warning=CANONICAL_WARNING,
         application=dict(brand_name="Old Tom Distillery",
                          class_type="Kentucky Straight Bourbon Whiskey",
                          alcohol_content="45% Alc./Vol.", net_contents="750 mL"),
         expected="PASS"),
    dict(key="stones_throw_caps",
         brand="STONE'S THROW", class_type="Small Batch Rye Whiskey",
         abv_text="50% Alc./Vol. (100 Proof)", net="750 mL",
         producer="Bottled by Stone's Throw Spirits, Louisville, KY",
         warning=CANONICAL_WARNING,
         application=dict(brand_name="Stone's Throw",  # title case in the application
                          class_type="Small Batch Rye Whiskey",
                          alcohol_content="50% Alc./Vol.", net_contents="750 mL"),
         expected="PASS (Dave's case: STONE'S THROW vs Stone's Throw resolves via fuzzy match)"),
    dict(key="silver_creek_titlecase_warning",
         brand="Silver Creek", class_type="Tennessee Whiskey",
         abv_text="40% Alc./Vol. (80 Proof)", net="750 mL",
         producer="Silver Creek Distilling Co., Lynchburg, TN",
         warning=CANONICAL_WARNING.replace("GOVERNMENT WARNING:", "Government Warning:"),
         application=dict(brand_name="Silver Creek", class_type="Tennessee Whiskey",
                          alcohol_content="40% Alc./Vol.", net_contents="750 mL"),
         expected="MISMATCH on warning (title case, not all caps — Jenny's catch)"),
    dict(key="copper_ridge_no_warning",
         brand="Copper Ridge", class_type="Single Malt Whiskey",
         abv_text="46% Alc./Vol. (92 Proof)", net="750 mL",
         producer="Copper Ridge Distillery, Asheville, NC",
         warning="",  # warning omitted entirely
         application=dict(brand_name="Copper Ridge", class_type="Single Malt Whiskey",
                          alcohol_content="46% Alc./Vol.", net_contents="750 mL"),
         expected="MISSING warning (must be rejected)"),
    dict(key="harbor_gin_wrong_abv",
         brand="Harbor Light", class_type="London Dry Gin",
         abv_text="47% Alc./Vol. (94 Proof)", net="750 mL",
         producer="Harbor Light Distillers, Portland, ME",
         warning=CANONICAL_WARNING,
         application=dict(brand_name="Harbor Light", class_type="London Dry Gin",
                          alcohol_content="40% Alc./Vol.",  # application says 40, label 47
                          net_contents="750 mL"),
         expected="MISMATCH on alcohol_content (application 40% vs label 47%)"),
    dict(key="hopworks_ipa_beer_no_abv",
         brand="Hopworks", class_type="India Pale Ale",
         abv_text="",  # no ABV — legal for a malt beverage
         net="12 FL OZ",
         producer="Brewed & Bottled by Hopworks Brewing Co., Portland, OR",
         warning=CANONICAL_WARNING,
         application=None,
         expected="PASS (beer: alcohol content is optional for malt beverages)"),
    dict(key="silverleaf_cabernet_wine",
         brand="Silverleaf", class_type="Napa Valley Cabernet Sauvignon",
         abv_text="13.5% Alc./Vol.", net="750 mL",
         producer="Produced & Bottled by Silverleaf Cellars, Napa, CA · CONTAINS SULFITES",
         warning=CANONICAL_WARNING,
         application=None,
         expected="PASS (wine: ABV required and present)"),
]


def main():
    manifest, expected = {}, []
    for s in SCENARIOS:
        img = render(s["brand"], s["class_type"], s["abv_text"], s["net"],
                     s["producer"], s["warning"])
        fname = f"{s['key']}.jpg"
        img.save(os.path.join(HERE, fname), quality=92)
        if s["application"] is not None:
            manifest[fname] = s["application"]
        expected.append(f"- **{fname}** → {s['expected']}")

    # A tilted, dimmed variant of the good label to exercise preprocessing.
    good = render(SCENARIOS[0]["brand"], SCENARIOS[0]["class_type"],
                  SCENARIOS[0]["abv_text"], SCENARIOS[0]["net"],
                  SCENARIOS[0]["producer"], SCENARIOS[0]["warning"])
    tilted = good.rotate(-12, expand=True, fillcolor="#cfc7b2")
    tilted = tilted.point(lambda p: int(p * 0.72))  # dim it ~28%
    tilted.save(os.path.join(HERE, "old_tom_tilted.jpg"), quality=92)
    manifest["old_tom_tilted.jpg"] = SCENARIOS[0]["application"]
    expected.append("- **old_tom_tilted.jpg** → PASS (rotated + dimmed; tests "
                    "preprocessing + model robustness)")

    with open(os.path.join(HERE, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    with open(os.path.join(HERE, "expected_results.md"), "w") as f:
        f.write("# Expected results for sample labels\n\n" + "\n".join(expected) + "\n")

    print(f"Generated {len(manifest)} sample labels in {HERE}")
    for k in manifest:
        print(" ", k)


if __name__ == "__main__":
    main()
