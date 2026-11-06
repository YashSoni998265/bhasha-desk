"""Unicode script ranges for Indian languages, and a display-length measure."""

from __future__ import annotations

import unicodedata

SCRIPT_RANGES = {
    "Devanagari": [(0x0900, 0x097F), (0xA8E0, 0xA8FF)],
    "Bengali": [(0x0980, 0x09FF)],
    "Gurmukhi": [(0x0A00, 0x0A7F)],
    "Gujarati": [(0x0A80, 0x0AFF)],
    "Odia": [(0x0B00, 0x0B7F)],
    "Tamil": [(0x0B80, 0x0BFF)],
    "Telugu": [(0x0C00, 0x0C7F)],
    "Kannada": [(0x0C80, 0x0CFF)],
    "Malayalam": [(0x0D00, 0x0D7F)],
}

# The danda and double danda live in the Devanagari block but end sentences
# in several Indian scripts.
SHARED = [(0x0964, 0x0965)]

# Zero-width joiners shape conjuncts; they are not letters of another script.
JOINERS = {"‌", "‍"}


def _in(ranges: list[tuple[int, int]], ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in ranges)


def is_latin(ch: str) -> bool:
    return ("A" <= ch <= "Z") or ("a" <= ch <= "z") or ("À" <= ch <= "ɏ")


def in_script(ch: str, script: str) -> bool:
    if script not in SCRIPT_RANGES:
        raise ValueError(f"unknown script {script!r}")
    return ch in JOINERS or _in(SHARED, ch) or _in(SCRIPT_RANGES[script], ch)


def script_name(ch: str) -> str:
    if is_latin(ch):
        return "Latin"
    for name, ranges in SCRIPT_RANGES.items():
        if _in(ranges, ch):
            return name
    try:
        return unicodedata.name(ch).split(" ")[0].title()
    except ValueError:
        return "Unknown"


def visual_length(text: str) -> int:
    """Approximate how many characters a reader sees.

    Combining marks (vowel signs, viramas) attach to the letter before them,
    so they are not counted. This is close to a grapheme count for Indian
    scripts and needs nothing outside the standard library.
    """
    return sum(1 for ch in text if unicodedata.category(ch) not in ("Mn", "Mc", "Me", "Cf"))
