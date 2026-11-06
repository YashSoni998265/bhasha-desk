"""What CI checks, decided by the branch a pull request comes from.

    l10n/r<R>          the release pull request the release agent opens:
                       the full release gate (`verify_tree`)
    l10n/r<R>-<lang>   one language's branch: that language passes every check
    anything else      data health, and every translation that exists passes
                       (strings still to translate aren't failures on main)

Kept in Python rather than in the workflow file so it is tested like the rest.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from bhasha import doctor, report
from bhasha.catalog import Catalog
from bhasha.checker import check, errors
from bhasha.glossary import rules_for
from bhasha.release import verify_tree


def classify(head_ref: Optional[str], known: set) -> tuple:
    """("release", R, None), ("language", R, code) or ("default", None, None)."""
    ref = (head_ref or "").strip()
    if not ref.startswith("l10n/r") or "/" in ref[len("l10n/r"):]:
        return ("default", None, None)
    rest = ref[len("l10n/r"):]
    if not rest:
        return ("default", None, None)
    rel, _, code = rest.rpartition("-")
    if rel and code in known:
        return ("language", rel, code)
    return ("release", rest, None)


def _language_findings(cat: Catalog, code: str, allow_missing: bool) -> list:
    info = cat.language(code)
    found = check(cat.english, cat.meta, cat.locale(code), info["script"], rules_for(cat.read, code))
    return [f for f in found if not (allow_missing and f.rule == "missing")]


def run(root: Path, head_ref: Optional[str]) -> tuple:
    """Returns (ok, chat text, Markdown summary)."""
    cat = Catalog.at(root)
    mode, release, code = classify(head_ref, set(cat.languages))

    if mode == "release":
        result = verify_tree(root, release)
        return (result["ok"], report.verify_text(result),
                "## Bhasha Desk release gate\n\n" + report.verify_markdown(result))

    health = doctor.data_checks(root)
    codes = [code] if mode == "language" else cat.target_languages()
    blocks, rows, ok = [report.doctor_text(health)], [], not doctor.failed(health)
    for c in codes:
        found = errors(_language_findings(cat, c, allow_missing=(mode == "default")))
        ok = ok and not found
        name = cat.language(c)["name"]
        if found:
            blocks.append(f"{report.FAIL} {name} ({c}): " + "; ".join(f"{f.key}: {f.message}" for f in found))
            rows.append(f"| {name} ({c}) | {report.FAIL} {len(found)} problems: "
                        + "; ".join(f"`{f.key}` {f.message}" for f in found[:5]) + " |")
        else:
            missing = sum(1 for k in cat.english if k not in cat.locale(c))
            note = f", {missing} still to translate" if missing else ""
            blocks.append(f"{report.OK} {name} ({c}): every translation passes{note}")
            rows.append(f"| {name} ({c}) | {report.OK} every translation passes{note} |")
    title = f"one language ({code}) on release {release}" if mode == "language" else "translations and data"
    markdown = "\n".join([f"## Bhasha Desk checks: {title}", "", report.doctor_markdown(health), "",
                          "| Language | Result |", "|---|---|", *rows])
    return ok, "\n".join(blocks), markdown
