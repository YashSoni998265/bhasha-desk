"""`bhasha demo run`: a whole release, replayed without Switch and without AI.

It builds a throwaway "GitHub" (a bare git repository) and three agent
checkouts next to it, then plays release 2.4 the way the agents do in chat,
calling the same functions their `bhasha` commands call, with canned
translations standing in for the language model:

    open the release → drafts in two languages → a reviewer's rule → the rule
    recorded once and enforced in every language → approvals bound to the exact
    strings → an edit after approval that the gate refuses → ship → the CI gate
    on a clean clone → a tampered string that CI catches

It shows the deterministic half: what Bhasha Desk guarantees no matter what
the model writes. The conversation itself is the part that needs Switch.
"""

from __future__ import annotations

import functools
import http.server
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional

from bhasha import BhashaError, ci, gitops, release
from bhasha.catalog import Catalog, find_root
from bhasha.checker import errors
from bhasha.demo_data import (HINDI_DRAFT, HINDI_KEEP_AUTOPAY, HINDI_LATE_EDIT, TAMIL_DRAFT,
                              TAMIL_KEEP_AUTOPAY)
from bhasha.integrity import short

R = "2.4"
WHO = {"scout": "bhasha-scout", "translator": "bhasha-translator", "release": "bhasha-release",
       "ci": "CI", "Arjun": "Arjun (reviewer)", "Priya": "Priya (reviewer)"}


def say(who: str, room: str, text: str, mark: str = "") -> None:
    room_part = f"{room:<13}" if room else " " * 13
    print(f"{WHO.get(who, who):<18} {room_part} {text}" + (f"   ← {mark}" if mark else ""))


def detail(lines) -> None:
    for line in lines:
        print(" " * 33 + line)


def _git(cwd: Path, *args: str) -> str:
    return gitops.git(cwd, *args)


def _apply(wt: Path, code: str, strings: dict) -> dict:
    from bhasha.cli import apply_translations  # the same code `bhasha apply` runs
    return apply_translations(wt, code, strings)


def findings_for(cat: Catalog, code: str) -> list:
    from bhasha.cli import findings_for as cli_findings  # the same checks `bhasha check` runs
    return cli_findings(cat, code)


def build_world(source: Path, home: Path) -> dict:
    """A bare 'GitHub' seeded from this checkout's app, and three agent checkouts."""
    seed = home / "seed"
    for rel in ("languages.json", "demo-app", "glossary"):
        src, dst = source / rel, seed / rel
        if src.is_dir():
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("approvals.json"))
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, dst)
    _git(home, "init", "-q", "-b", "main", str(seed))
    for k, v in (("user.name", "PaySathi team"), ("user.email", "team@paysathi.example")):
        _git(seed, "config", k, v)
    _git(seed, "add", ".")
    _git(seed, "commit", "-q", "-m", "PaySathi before release 2.4")
    origin = home / "github.git"
    _git(home, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-q", "origin", "main")
    clones = {}
    for name in ("scout", "translator", "release"):
        (home / "agents" / name).mkdir(parents=True)
        clone = home / "agents" / name / "bhasha-desk"
        _git(home, "clone", "-q", str(origin), str(clone))
        _git(clone, "config", "user.name", f"bhasha-{name}")
        _git(clone, "config", "user.email", f"bhasha-{name}@users.noreply.example")
        clones[name] = clone
    return {"origin": origin, **clones}


def run(source: Optional[Path] = None, keep: Optional[Path] = None, serve: bool = False, port: int = 8080) -> int:
    began = time.monotonic()
    source = find_root(source)
    home = Path(keep).resolve() if keep else Path(tempfile.mkdtemp(prefix="bhasha-demo-"))
    if keep:
        if home.exists() and any(home.iterdir()):
            raise BhashaError(f"{home} isn't empty; pass an empty or new folder to --keep")
        home.mkdir(parents=True, exist_ok=True)
    w = build_world(source, home)
    scout, translator, shipper = w["scout"], w["translator"], w["release"]
    checks_caught, stale_refused, ci_caught = [], False, False

    print(f"Bhasha Desk replay · release {R} · no Switch and no AI: canned translations through the code the agents run")
    print(f"A local 'GitHub' and three agent checkouts in {home}\n")

    # 1. The scout opens the release.
    opened = release.start(scout, R, by="Yash")
    _git(Path(opened["worktree"]), "push", "-q", "-u", "origin", release.branch(R))
    new = sum(1 for m in Catalog.at(Path(opened["worktree"])).meta.values() if m.get("since") == R)
    say("scout", "#bhasha-hub", f"release {R} opened by Yash · {new} new strings · branch {release.branch(R)} pushed")

    # 2. Drafts in two languages, each in its own worktree.
    worktrees = {}
    for code, draft, room in (("ta", TAMIL_DRAFT, "#bhasha-ta"), ("hi", HINDI_DRAFT, "#bhasha-hi")):
        wt = Path(release.lang(translator, R, code)["worktree"])
        worktrees[code] = wt
        res = _apply(wt, code, draft)
        _git(wt, "push", "-q", "-u", "origin", release.branch(R, code))
        state = "✅ all checks pass" if not errors(res["findings"]) else f"❌ {len(errors(res['findings']))} problems"
        say("translator", room, f"{res['name']} drafts: {len(res['changed'])} strings · {state} · \"AutoPay\" written as "
                                f"\"{draft['autopay.setup_cta'].split()[0]}\"")

    # 3. A reviewer's correction becomes a rule, recorded once for every language.
    say("Arjun", "#bhasha-ta", '"AutoPay is our product name, keep it in English everywhere."')
    say("translator", "#bhasha-ta", "\"Got it, I'll treat that as a rule\" → tells the scout in #bhasha-hub")
    from bhasha.cli import record_decision
    decided = record_decision(scout, "common", "AutoPay", "keep", "Arjun", "bhasha-ta", rel=R,
                              note="Our product name.")
    _git(Path(decided["worktree"]), "push", "-q", "origin", release.branch(R))
    say("scout", "#bhasha-hub", f"glossary: {decided['change']} ({decided['where']}), decided by Arjun in bhasha-ta · pushed")

    # 4. Every language syncs; the check points at exactly what to change.
    for code, fix, room in (("ta", TAMIL_KEEP_AUTOPAY, "#bhasha-ta"), ("hi", HINDI_KEEP_AUTOPAY, "#bhasha-hi")):
        release.sync(translator, R, code)
        flagged = errors(findings_for(Catalog.at(worktrees[code]), code))
        say("translator", room, f"sync → ❌ {len(flagged)} strings break the new rule")
        detail(f"• {f.key}: {f.message}" for f in flagged)
        checks_caught.append(len(flagged))
        res = _apply(worktrees[code], code, fix)
        _git(worktrees[code], "push", "-q", "origin", release.branch(R, code))
        say("translator", room, f"{len(res['changed'])} strings fixed · ✅ all checks pass",
            "one correction, every language" if code == "hi" else "")

    # 5. Reviewers approve exactly what they saw.
    for code, who, room in (("ta", "Arjun", "#bhasha-ta"), ("hi", "Priya", "#bhasha-hi")):
        a = release.approve(worktrees[code], R, code, who, room=room.lstrip("#"))
        _git(worktrees[code], "push", "-q", "origin", release.branch(R, code))
        say(who, room, f"approve → ✅ recorded, bound to strings {short(a['strings_sha256'])}")

    # 6. An edit after approval: the gate refuses until the reviewer approves again.
    res = _apply(worktrees["hi"], "hi", HINDI_LATE_EDIT)
    _git(worktrees["hi"], "push", "-q", "origin", release.branch(R, "hi"))
    say("translator", "#bhasha-hi", f"edits one string after Priya approved (\"{HINDI_DRAFT['autopay.pause_cta']}\" → "
                                    f"\"{HINDI_LATE_EDIT['autopay.pause_cta']}\")")
    try:
        release.merge(shipper, R)
        raise BhashaError("the gate should have refused a stale approval")
    except BhashaError as exc:
        say("release", "#bhasha-ship", f"ship {R} → refused: {str(exc).split('. ')[0]}", "the gate holding")
        detail([str(exc).split("(", 1)[-1].rstrip(")")])
        stale_refused = True
    a = release.approve(worktrees["hi"], R, "hi", "Priya", room="bhasha-hi")
    _git(worktrees["hi"], "push", "-q", "origin", release.branch(R, "hi"))
    say("Priya", "#bhasha-hi", f"approve → ✅ recorded again, bound to strings {short(a['strings_sha256'])}")

    # 7. Ship: merge, verify, notes.
    merged = release.merge(shipper, R)
    _git(Path(merged["worktree"]), "push", "-q", "-u", "origin", release.branch(R))
    say("release", "#bhasha-ship", f"ship {R} → merged {', '.join(merged['languages'])} · ✅ verified · release notes written · pushed")

    # 8. CI on a clean clone of the pushed branch, then someone tampers with an approved string.
    ci_box = home / "ci"
    _git(home, "clone", "-q", "--branch", release.branch(R), str(w["origin"]), str(ci_box))
    ok, text, _ = ci.run(ci_box, release.branch(R))
    say("ci", "pull request", "l10n gate on a clean clone → " + text.splitlines()[0].split(" · ")[-1])
    ta_file = ci_box / "demo-app/locales/ta.json"
    original = ta_file.read_text(encoding="utf-8")
    ta_file.write_text(original.replace("இடைநிறுத்து", "நிறுத்து"), encoding="utf-8")
    ok_after, text_after, _ = ci.run(ci_box, release.branch(R))
    bad = [line for line in text_after.splitlines() if line.startswith("❌ Tamil")]
    say("ci", "pull request", "someone edits an approved Tamil string → " + ("❌ " + bad[0].split(" · ")[-1] if bad else "?"),
        "caught" if not ok_after else "")
    ci_caught = not ok_after
    ta_file.write_text(original, encoding="utf-8")

    notes = Path(merged["notes"])
    timeline = [line for line in notes.read_text(encoding="utf-8").splitlines() if line.startswith("- ") and " UTC · " in line]
    caught = [f"{sum(checks_caught)} strings that broke the new rule ({checks_caught[0]} Tamil, {checks_caught[1]} Hindi)"]
    if stale_refused:
        caught.append("an edit made after its approval, refused at ship")
    if ci_caught:
        caught.append("an approved string edited later, failed in CI")
    print(f"\nDone in {time.monotonic() - began:.1f} s. Caught along the way: " + "; ".join(caught) + ".")
    print(f"Release notes (the pull request body): {notes}")
    print("Their timeline:")
    detail(timeline)
    if not serve:
        print("\nSee the shipped app, with each approval verified in your browser: uv run bhasha demo run --serve")
        return 0
    app = Path(merged["worktree"]) / "demo-app"
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(app))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"\nThe shipped app: http://127.0.0.1:{port}/?lang=ta  (try हिन्दी too, and \"Try it: change one word\"). Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
