"""Glossary decisions: what reviewers decided, who decided it, and where.

Each file keeps two halves, after the learning-loop pattern in the Switch
expert's playbook: `terms` is the current truth (one rule per term and kind),
and `log` is an append-only record of every change, so you can always see
what was believed when and who said so.

Rule kinds:
  keep    the English term stays exactly as written (brand and product names)
  prefer  when the English term appears, the translation must use `translation`
  avoid   the translation must never contain `translation`
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from bhasha import BhashaError
from bhasha.catalog import Reader, write_json

KINDS = ("keep", "prefer", "avoid")


@dataclass
class Rule:
    term: str
    rule: str
    translation: Optional[str]
    note: str
    decided_by: str
    decided_in: str
    decided_at: str
    release: Optional[str]
    scope: str = "common"

    def describe(self) -> str:
        if self.rule == "keep":
            return f'keep "{self.term}" in English'
        if self.rule == "prefer":
            return f'use "{self.translation}" for "{self.term}"'
        return f'don\'t use "{self.translation}" (for "{self.term}")'

    def provenance(self) -> str:
        if self.decided_in in ("seed", ""):
            return f"from {self.decided_by}"
        return f"decided by {self.decided_by} in {self.decided_in}"


def path_for(scope: str) -> str:
    return f"glossary/{scope}.json"


def load(read: Reader, scope: str) -> dict:
    text = read(path_for(scope))
    if text is None:
        return {"language": scope, "terms": [], "log": []}
    return json.loads(text)


def rules_for(read: Reader, lang: str) -> list:
    """The rules that apply to one language: common rules, then its own."""
    rules = []
    for scope in ("common", lang):
        data = load(read, scope)
        for t in data.get("terms", []):
            fields = {k: t.get(k) for k in ("term", "rule", "translation", "note", "decided_by", "decided_in", "decided_at", "release")}
            fields["note"] = fields["note"] or ""
            rules.append(Rule(scope=scope, **fields))
    return rules


def add_rule(
    root: Path,
    scope: str,
    term: str,
    rule: str,
    decided_by: str,
    room: str,
    translation: Optional[str] = None,
    note: str = "",
    release: Optional[str] = None,
    now: Optional[str] = None,
) -> tuple:
    """Record a decision. Returns (rule, change description)."""
    if rule not in KINDS:
        raise BhashaError(f"rule must be one of {', '.join(KINDS)}")
    term = term.strip()
    if not term:
        raise BhashaError("the term can't be empty")
    if rule in ("prefer", "avoid") and not (translation or "").strip():
        raise BhashaError(f"a {rule} rule needs --translation")
    if rule != "keep" and scope == "common":
        raise BhashaError(f"a {rule} rule is about one language; use --lang <code>, not common")
    if not decided_by.strip():
        raise BhashaError("say who decided it with --decided-by")

    stamp = now or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    file = root / path_for(scope)
    data = json.loads(file.read_text(encoding="utf-8")) if file.is_file() else {"language": scope, "terms": [], "log": []}
    new = Rule(term, rule, translation if rule != "keep" else None, note, decided_by, room, stamp, release, scope)

    def same(t: dict) -> bool:
        if t["term"] != term or t["rule"] != rule:
            return False
        return rule != "avoid" or t.get("translation") == translation

    replaced = [t for t in data["terms"] if same(t)]
    data["terms"] = [t for t in data["terms"] if not same(t)]
    entry = asdict(new)
    entry.pop("scope")
    data["terms"].append(entry)
    change = ("replace " if replaced else "") + new.describe()
    data["log"].append({"at": stamp, "by": decided_by, "room": room, "release": release, "change": change, "note": note})
    write_json(file, data)
    return new, change
