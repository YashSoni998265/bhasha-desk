"""Deterministic checks for every translated string.

These are the rules a language model must not be trusted to remember. Each
one is tested in tests/test_checker.py in the failing direction too.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Iterable, Optional

from bhasha.glossary import Rule
from bhasha.scripts import in_script, is_latin, script_name, visual_length

PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
TAG = re.compile(r"</?([A-Za-z][A-Za-z0-9]*)\b[^>]*>")
LATIN_WORD = re.compile(r"[A-Za-zÀ-ɏ][A-Za-z0-9À-ɏ'’\-]*")


@dataclass
class Finding:
    key: str
    rule: str
    message: str
    severity: str = "error"

    def as_dict(self) -> dict:
        return asdict(self)


def placeholders(text: str) -> list:
    return sorted(PLACEHOLDER.findall(text))


def tags(text: str) -> list:
    return sorted(m.group(0).lower().replace(" ", "") for m in TAG.finditer(text))


def _strip(text: str) -> str:
    return TAG.sub(" ", PLACEHOLDER.sub(" ", text))


def _contains_term(text: str, term: str) -> bool:
    """Whole-word, case-sensitive match, so "UPI" doesn't match "UPIs"."""
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", text) is not None


def _mentions(text: str, term: str) -> bool:
    """Case-insensitive whole-word match for prefer rules."""
    return re.search(rf"(?<![A-Za-z]){re.escape(term)}(?![A-Za-z])", text, re.IGNORECASE) is not None


def check(
    english: dict,
    meta: dict,
    translations: dict,
    script: str,
    rules: Iterable[Rule] = (),
    keys: Optional[Iterable[str]] = None,
) -> list:
    rules = list(rules)
    keep_terms = [r.term for r in rules if r.rule == "keep"]
    scope = list(keys) if keys is not None else list(english)
    findings = []

    for key in scope:
        if key not in english:
            findings.append(Finding(key, "unknown-key", "not in en.json"))
            continue
        source = english[key]
        if key not in translations:
            findings.append(Finding(key, "missing", "not translated yet"))
            continue
        text = translations[key]
        if not isinstance(text, str) or not text.strip():
            findings.append(Finding(key, "empty", "the translation is empty"))
            continue

        source_words = [w for w in LATIN_WORD.findall(_strip(source)) if not any(w in t.split() or w == t for t in keep_terms)]
        if text.strip() == source.strip() and source_words:
            findings.append(Finding(key, "untranslated", "still in English"))
            continue

        want, got = placeholders(source), placeholders(text)
        for name in sorted(set(want) - set(got)):
            findings.append(Finding(key, "placeholder", f"placeholder {{{name}}} is missing"))
        for name in sorted(set(got) - set(want)):
            findings.append(Finding(key, "placeholder", f"placeholder {{{name}}} isn't in the English"))

        if tags(source) != tags(text):
            findings.append(Finding(key, "tags", f"tags must match the English: {' '.join(tags(source)) or 'none'}"))

        limit = (meta.get(key) or {}).get("max_len")
        if limit:
            length = visual_length(_strip(text).strip())
            if length > limit:
                findings.append(Finding(key, "length", f"too long: {length} characters, limit {limit}"))

        clean = _strip(text)
        for term in sorted(keep_terms, key=len, reverse=True):
            clean = clean.replace(term, " ")
        leftovers = sorted(set(LATIN_WORD.findall(clean)))
        if leftovers:
            findings.append(Finding(key, "english-left", "English left in the translation: " + ", ".join(leftovers)))
        foreign = sorted({script_name(ch) for ch in clean if unicodedata.category(ch)[0] in "LM" and not is_latin(ch) and not in_script(ch, script)})
        if foreign:
            findings.append(Finding(key, "script", f"letters from {', '.join(foreign)}; expected {script}"))

        for r in rules:
            if r.rule == "keep" and _contains_term(source, r.term) and not _contains_term(text, r.term):
                findings.append(Finding(key, "glossary", f"{r.describe()} ({r.provenance()})"))
            elif r.rule == "prefer" and _mentions(source, r.term) and r.translation not in text:
                findings.append(Finding(key, "glossary", f"{r.describe()} ({r.provenance()})"))
            elif r.rule == "avoid" and r.translation and r.translation in text:
                findings.append(Finding(key, "glossary", f"{r.describe()} ({r.provenance()})"))

    # The same English in two places usually wants the same words. Only a
    # warning: context can call for different ones.
    by_source = {}
    for key in scope:
        if key in english and isinstance(translations.get(key), str) and translations[key].strip():
            by_source.setdefault(english[key].strip(), []).append(key)
    for same in by_source.values():
        for other in same[1:]:
            if translations[other].strip() != translations[same[0]].strip():
                findings.append(Finding(other, "consistency", f"same English as {same[0]} but translated differently", "warning"))

    if keys is None:
        for key in translations:
            if key not in english:
                findings.append(Finding(key, "unknown-key", "not in en.json, so the app never shows it", "warning"))
    return findings


def errors(findings: Iterable[Finding]) -> list:
    return [f for f in findings if f.severity == "error"]
