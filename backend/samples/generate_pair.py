"""Generate a clean front-label + back-label pair — the way applicants actually
submit to COLA (separate cropped label images per panel). Demonstrates the
multi-image merge on realistic, non-bottle-photo input.

Run:  python samples/generate_pair.py
"""

from __future__ import annotations

import os

from generate_samples import CANONICAL_WARNING, render

HERE = os.path.dirname(__file__)

# Front (brand) label: brand, class, ABV, net contents — no warning.
front = render(
    brand="Old Tom Distillery",
    class_type="Kentucky Straight Bourbon Whiskey",
    abv_text="45% Alc./Vol. (90 Proof)",
    net="750 mL",
    producer="",
    warning="",
)
front.save(os.path.join(HERE, "pair_front_label.jpg"), quality=92)

# Back label: government warning + bottler name/address.
back = render(
    brand="",
    class_type="",
    abv_text="",
    net="",
    producer="Distilled & Bottled by Old Tom Distillery, Bardstown, KY",
    warning=CANONICAL_WARNING,
)
back.save(os.path.join(HERE, "pair_back_label.jpg"), quality=92)

print("Generated pair_front_label.jpg + pair_back_label.jpg")
