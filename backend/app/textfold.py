"""Unicode normalization for adversarial text.

Red-teaming showed two evasion vectors that rely on characters that *look* like
ASCII but aren't: Unicode homoglyphs in injection text (to dodge the keyword
detector) and letter-for-digit swaps in numeric fields ("4O%" for "40%"). Azure
OCR happens to normalize many of these to ASCII because it reads by shape — but
relying on that is fragile. These helpers fold confusables in our own code so the
defense holds regardless of OCR behavior.
"""

from __future__ import annotations

import re
import unicodedata

# Cyrillic / Greek (and a few others) that render identically to ASCII letters.
_CONFUSABLES = {
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "і": "i", "ѕ": "s", "ј": "j", "ԁ": "d", "ո": "n", "м": "m", "т": "t",
    "А": "A", "Е": "E", "О": "O", "Р": "P", "С": "C", "Т": "T", "В": "B",
    "Н": "H", "М": "M", "К": "K", "Х": "X", "У": "Y", "І": "I", "Ѕ": "S",
    "ε": "e", "ο": "o", "ρ": "p", "τ": "t", "α": "a", "ν": "v", "ι": "i",
}

# Letters commonly used to impersonate digits (for numeric-field parsing only).
_NUMERIC = {"O": "0", "o": "0", "l": "1", "I": "1", "|": "1",
            "S": "5", "B": "8", "Z": "2"}
# Only fold a letter to a digit when it sits next to a real digit, so unit text
# like "ml" / "Liter" is left intact while "4O%" / "9O Proof" is corrected.
_NUM_ADJACENT = re.compile(r"(?<=\d)[OolI|SBZ]|[OolI|SBZ](?=\d)")


def fold_for_detection(text: str | None) -> str:
    """NFKC-normalize and map confusable letters to ASCII, so homoglyph-laced
    injection text matches the same patterns plain text would."""
    if not text:
        return text or ""
    t = unicodedata.normalize("NFKC", text)
    return "".join(_CONFUSABLES.get(ch, ch) for ch in t)


def fold_numeric(text: str | None) -> str:
    """Map letter-shaped digits to digits when adjacent to a real digit — used
    only when parsing numeric fields, so '4O%' parses as '40%' while unit text
    ('750 mL', 'Liter') is left intact. For parsing only, never for display."""
    if not text:
        return text or ""
    return _NUM_ADJACENT.sub(lambda m: _NUMERIC.get(m.group(0), m.group(0)), text)
