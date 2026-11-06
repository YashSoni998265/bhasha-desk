"""The same results, written twice: lines for chat, Markdown for GitHub.

Chat text has no tables (Slack doesn't render them) and one line per item.
Markdown goes to the pull request's CI summary.
"""

from __future__ import annotations

from bhasha.integrity import short

OK, FAIL, WAIT, WARN = "✅", "❌", "⏳", "⚠️"
LEVEL = {"ok": OK, "warn": WARN, "fail": FAIL}


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def doctor_text(results: list) -> str:
    fails = [r for r in results if r["level"] == "fail"]
    warns = [r for r in results if r["level"] == "warn"]
    if fails:
        head = f"{FAIL} {plural(len(fails), 'problem')} to fix before the agents start"
    else:
        head = f"{OK} ready" + (f", {plural(len(warns), 'warning')}" if warns else "")
    lines = [f"doctor · {plural(len(results), 'check')} · {head}"]
    lines += [f"{LEVEL[r['level']]} {r['check']}: {r['detail']}" for r in results]
    return "\n".join(lines)


def doctor_markdown(results: list) -> str:
    lines = ["| Check | | Detail |", "|---|---|---|"]
    lines += [f"| {r['check']} | {LEVEL[r['level']]} | {r['detail']} |" for r in results]
    return "\n".join(lines)


def _approver(approval: dict) -> str:
    where = f" in {approval['approved_in']}" if approval.get("approved_in") else ""
    return f"{approval.get('approved_by', '?')}{where}"


def verify_text(result: dict) -> str:
    r = result["release"]
    head = (f"{OK} every language is translated, checked and approved" if result["ok"]
            else f"{FAIL} not ready to ship")
    lines = [f"Release {r} · verify · {head}"]
    for row in result["languages"]:
        label = f"{row['name']} ({row['language']}) · {row['translated']}/{row['total']}"
        if row["ok"]:
            lines.append(f"{OK} {label} · approved by {_approver(row['approval'])} · strings {short(row['strings_sha256'])}")
        else:
            lines.append(f"{FAIL} {label} · {'; '.join(row['problems'])}")
    if not result["languages"]:
        lines.append(f"{FAIL} no languages to ship")
    if not result.get("manifest"):
        lines.append(f"{WARN} no release manifest (releases/r{r}/RELEASE.json); was the release opened with bhasha release start?")
    return "\n".join(lines)


def verify_markdown(result: dict) -> str:
    r = result["release"]
    lines = [f"### Release {r}: " + ("every language translated, checked and approved ✅" if result["ok"] else "not ready to ship ❌"),
             "", "| Language | Strings | Approved by | Strings digest | Result |", "|---|---|---|---|---|"]
    for row in result["languages"]:
        a = row["approval"] or {}
        who = _approver(a) if a else "-"
        lines.append(f"| {row['name']} ({row['language']}) | {row['translated']}/{row['total']} | {who} | "
                     f"`{short(row['strings_sha256'])}` | {OK if row['ok'] else FAIL + ' ' + '; '.join(row['problems'])} |")
    lines += ["", "An approval counts only while the SHA-256 of the strings and of the English it was given for "
                  "still match. Recomputed here by `bhasha release verify`."]
    return "\n".join(lines)
