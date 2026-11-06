"""Content digests that bind an approval to exactly what the reviewer saw.

An approval records a SHA-256 of the language's strings and of the English
(with its context and limits) they translate. If either changes afterwards,
the approval no longer counts: `bhasha release status` reports it as stale,
and `bhasha release verify` (which CI runs on the release pull request) fails.

Digests are taken over canonical JSON (sorted keys, no whitespace, UTF-8), so
re-indenting or reordering a file doesn't change them; changing a character does.
"""

from __future__ import annotations

import hashlib
import json
from typing import Iterable, Optional

VALID = "valid"


def digest(data) -> str:
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def strings_digest(translations: dict) -> str:
    return digest(translations)


def source_digest(english: dict, meta: dict) -> str:
    return digest({"english": english, "meta": meta})


def rules_digest(rules: Iterable) -> str:
    """The glossary rules in force for a language, for the record."""
    return digest(sorted([r.scope, r.term, r.rule, r.translation or ""] for r in rules))


def short(value: Optional[str]) -> str:
    return (value or "").split(":", 1)[-1][:12] or "none"


def approval_state(approval: Optional[dict], english: dict, meta: dict, translations: dict) -> Optional[str]:
    """None without an approval, VALID if it still matches, else why it doesn't."""
    if not approval:
        return None
    if "strings_sha256" not in approval or "source_sha256" not in approval:
        return "the approval has no digests, so it can't be verified; approve again"
    if approval["strings_sha256"] != strings_digest(translations):
        return "strings changed after the approval"
    if approval["source_sha256"] != source_digest(english, meta):
        return "the English changed after the approval"
    return VALID
