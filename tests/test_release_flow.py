"""The whole workflow with real git: three agents, three clones, one remote.

This is the demo, minus the chat: the scout opens release 2.4, a translator
works Tamil and Hindi in separate worktrees, a reviewer's decision about one
term reaches every language through the release branch, nothing ships until
every language is approved, and the release agent writes the notes.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from bhasha import BhashaError, release
from bhasha.cli import main

REPO = Path(__file__).resolve().parents[1]
ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}

TAMIL = {
    "autopay.title": "UPI ஆட்டோபே",
    "autopay.subtitle": "பில்களை ஒவ்வொரு மாதமும் தானாகச் செலுத்துங்கள்.",
    "autopay.setup_cta": "ஆட்டோபே அமைக்கவும்",
    "autopay.mandate_info": "{merchant}-க்கு மாதம் ₹{limit} வரையிலான ஆணையை அனுமதிக்கிறீர்கள்.",
    "autopay.next_debit": "அடுத்த பிடித்தம்: {date}",
    "autopay.pause_cta": "இடைநிறுத்து",
    "autopay.cancel_warning": "ரத்து செய்தால் <b>{merchant}</b>-க்கு இனி பணம் செலுத்தப்படாது.",
    "autopay.reminders.one": "ஒவ்வொரு பிடித்தத்திற்கும் முன் {count} நினைவூட்டல் வரும்.",
    "autopay.reminders.other": "ஒவ்வொரு பிடித்தத்திற்கும் முன் {count} நினைவூட்டல்கள் வரும்.",
    "autopay.limit_error": "₹1 முதல் ₹{max} வரை ஒரு தொகையை உள்ளிடவும்.",
}
HINDI = {
    "autopay.title": "UPI AutoPay",
    "autopay.subtitle": "हर महीने अपने बिल समय पर अपने-आप भरें।",
    "autopay.setup_cta": "AutoPay सेट करें",
    "autopay.mandate_info": "आप {merchant} के लिए हर महीने ₹{limit} तक के मैंडेट को मंज़ूरी दे रहे हैं।",
    "autopay.next_debit": "अगला डेबिट {date} को",
    "autopay.pause_cta": "रोकें",
    "autopay.cancel_warning": "रद्द करने पर <b>{merchant}</b> को आगे के सभी पेमेंट रुक जाएंगे।",
    "autopay.reminders.one": "हर डेबिट से पहले आपको {count} रिमाइंडर मिलेगा।",
    "autopay.reminders.other": "हर डेबिट से पहले आपको {count} रिमाइंडर मिलेंगे।",
    "autopay.limit_error": "₹1 से ₹{max} के बीच कोई रकम डालें।",
}


def sh(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env={**os.environ, **ENV})


def bhasha(*args):
    return main([str(a) for a in args])


@pytest.fixture
def team(tmp_path, monkeypatch):
    monkeypatch.delenv("BHASHA_WORKTREES", raising=False)
    seed = tmp_path / "seed"
    for rel in ("languages.json", "demo-app/locales", "glossary"):
        src, dst = REPO / rel, seed / rel
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, dst)
    sh(seed, "init", "-q", "-b", "main")
    sh(seed, "add", ".")
    sh(seed, "commit", "-q", "-m", "seed")
    origin = tmp_path / "origin.git"
    sh(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    sh(seed, "remote", "add", "origin", str(origin))
    sh(seed, "push", "-q", "origin", "main")
    clones = {}
    for name in ("scout", "translator", "release"):
        home = tmp_path / "agents" / name
        home.mkdir(parents=True)
        sh(home, "clone", "-q", str(origin), "bhasha-desk")
        clones[name] = home / "bhasha-desk"
    return clones


def write(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_release_from_start_to_notes(team, tmp_path, capsys):
    scout, translator, shipper = team["scout"], team["translator"], team["release"]

    # The scout opens the release, which records who opened it and what's new, and pushes the branch.
    started = release.start(scout, "2.4", by="Yash")
    manifest = json.loads((Path(started["worktree"]) / "releases/r2.4/RELEASE.json").read_text())
    assert manifest["opened_by"] == "Yash" and len(manifest["new_strings"]) == 10
    assert manifest["languages"] == ["hi", "ta"]
    sh(started["worktree"], "push", "-q", "-u", "origin", "l10n/r2.4")

    # Tamil: worktree, drafts, checks pass, then the reviewer approves.
    ta = Path(release.lang(translator, "2.4", "ta")["worktree"])
    with pytest.raises(BhashaError, match="check"):
        release.approve(ta, "2.4", "ta", "Arjun")  # nothing translated yet
    assert bhasha("-C", ta, "apply", "ta", write(tmp_path, "ta.json", TAMIL)) == 0
    release.approve(ta, "2.4", "ta", "Arjun")
    sh(ta, "push", "-q", "-u", "origin", "l10n/r2.4-ta")

    # Hindi isn't started, so the release can't ship.
    with pytest.raises(BhashaError, match="hi: not started"):
        release.merge(shipper, "2.4")

    # The Tamil reviewer decides AutoPay stays in English. The scout records it
    # once, on the release branch, for every language.
    assert bhasha("-C", scout, "glossary", "add", "--common", "--term", "AutoPay", "--rule", "keep",
                  "--decided-by", "Arjun", "--room", "bhasha-ta", "--release", "2.4") == 0
    sh(started["worktree"], "push", "-q", "origin", "l10n/r2.4")

    # The published decision counts at once: Tamil's old approval no longer ships,
    # and approving stale work is refused until the branch syncs.
    states = {r["language"]: r["state"] for r in release.status(shipper, "2.4")["languages"]}
    assert states == {"hi": "not started", "ta": "failing checks"}
    with pytest.raises(BhashaError, match="sync"):
        release.approve(ta, "2.4", "ta", "Arjun")
    assert bhasha("-C", translator, "release", "sync", "2.4", "ta") == 1
    assert "keep \"AutoPay\" in English (decided by Arjun in bhasha-ta)" in capsys.readouterr().out
    fixed = {"autopay.title": "UPI AutoPay", "autopay.setup_cta": "AutoPay அமைக்கவும்"}
    assert bhasha("-C", ta, "apply", "ta", write(tmp_path, "ta-fix.json", fixed)) == 0
    release.approve(ta, "2.4", "ta", "Arjun")
    sh(ta, "push", "-q", "origin", "l10n/r2.4-ta")

    # Hindi starts after the decision, so it already has the rule.
    hi = Path(release.lang(translator, "2.4", "hi")["worktree"])
    assert bhasha("-C", hi, "apply", "hi", write(tmp_path, "hi.json", HINDI)) == 0
    release.approve(hi, "2.4", "hi", "Priya")
    sh(hi, "push", "-q", "-u", "origin", "l10n/r2.4-hi")

    status = release.status(shipper, "2.4")
    assert {r["language"]: r["state"] for r in status["languages"]} == {"hi": "approved", "ta": "approved"}

    merged = release.merge(shipper, "2.4")
    assert merged["languages"] == ["hi", "ta"]
    assert merged["pr"] is None  # origin is a local bare repo, not GitHub
    notes = Path(merged["notes"]).read_text(encoding="utf-8")
    assert "| Tamil (ta) | 18/18 | Arjun |" in notes
    assert "| Hindi (hi) | 18/18 | Priya |" in notes
    assert 'keep "AutoPay" in English (every language), decided by Arjun in bhasha-ta' in notes
    # The timeline: who opened it, the decision, each approval with its revisions, and the lead time.
    assert "## Timeline" in notes
    assert "release 2.4 opened by Yash, 10 new strings" in notes
    assert "Tamil approved by Arjun (2 revisions, 1 glossary sync)" in notes
    assert "Hindi approved by Priya (1 revision)" in notes
    assert "From release opened to pull request: **" in notes
    sh(merged["worktree"], "push", "-q", "-u", "origin", "l10n/r2.4")

    # CI checks out the pushed branch on a clean machine and runs the same gate.
    ci_box = tmp_path / "ci"
    sh(tmp_path, "clone", "-q", "--branch", "l10n/r2.4", str(tmp_path / "origin.git"), str(ci_box))
    assert bhasha("-C", ci_box, "release", "verify", "2.4") == 0
    summary = tmp_path / "summary.md"
    assert bhasha("-C", ci_box, "ci", "--head-ref", "l10n/r2.4", "--summary", summary) == 0
    assert "every language translated, checked and approved" in summary.read_text()

    # Anyone editing an approved string after the fact fails the gate.
    ta_file = ci_box / "demo-app/locales/ta.json"
    data = json.loads(ta_file.read_text())
    data["autopay.pause_cta"] = "நிறுத்து"
    ta_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    capsys.readouterr()
    assert bhasha("-C", ci_box, "release", "verify", "2.4") == 1
    assert "Tamil (ta) · 18/18 · strings changed after the approval" in capsys.readouterr().out


def test_an_edit_after_approval_voids_it_until_the_reviewer_approves_again(team, tmp_path, capsys):
    scout, translator, shipper = team["scout"], team["translator"], team["release"]
    opened = release.start(scout, "2.4")["worktree"]
    assert bhasha("-C", scout, "glossary", "add", "--common", "--term", "AutoPay", "--rule", "keep",
                  "--decided-by", "Arjun", "--room", "bhasha-ta", "--release", "2.4") == 0
    sh(opened, "push", "-q", "-u", "origin", "l10n/r2.4")
    for code, strings, reviewer in (("ta", TAMIL, "Arjun"), ("hi", HINDI, "Priya")):
        wt = Path(release.lang(translator, "2.4", code)["worktree"])
        fixed = {k: v.replace("ஆட்டோபே", "AutoPay") for k, v in strings.items()}
        assert bhasha("-C", wt, "apply", code, write(tmp_path, f"{code}.json", fixed)) == 0
        assert bhasha("-C", wt, "release", "approve", "2.4", code, "--by", reviewer, "--room", f"bhasha-{code}") == 0
        sh(wt, "push", "-q", "-u", "origin", f"l10n/r2.4-{code}")
    states = {r["language"]: r["state"] for r in release.status(shipper, "2.4")["languages"]}
    assert states == {"hi": "approved", "ta": "approved"}

    # The translator changes one Tamil string after Arjun approved.
    ta = Path(release.lang(translator, "2.4", "ta")["worktree"])
    capsys.readouterr()
    assert bhasha("-C", ta, "apply", "ta", write(tmp_path, "edit.json", {"autopay.pause_cta": "நிறுத்து"})) == 0
    assert "Arjun approved the earlier strings, so that approval no longer counts" in capsys.readouterr().out
    sh(ta, "push", "-q", "origin", "l10n/r2.4-ta")

    report = {r["language"]: r for r in release.status(shipper, "2.4")["languages"]}
    assert report["ta"]["state"] == "approval stale"
    assert report["ta"]["approval_state"] == "strings changed after the approval"
    with pytest.raises(BhashaError, match="ta: approval stale"):
        release.merge(shipper, "2.4")

    # Arjun approves the new wording; now it ships, with where each approval happened.
    assert bhasha("-C", ta, "release", "approve", "2.4", "ta", "--by", "Arjun", "--room", "bhasha-ta") == 0
    sh(ta, "push", "-q", "origin", "l10n/r2.4-ta")
    notes = Path(release.merge(shipper, "2.4")["notes"]).read_text(encoding="utf-8")
    assert "| Tamil (ta) | 18/18 | Arjun | bhasha-ta |" in notes


def test_ci_picks_the_check_from_the_branch(team, tmp_path):
    from bhasha.ci import classify

    known = {"hi", "ta", "bn"}
    assert classify("l10n/r2.4", known) == ("release", "2.4", None)
    assert classify("l10n/r2.4-trial", known) == ("release", "2.4-trial", None)
    assert classify("l10n/r2.4-trial-ta", known) == ("language", "2.4-trial", "ta")
    assert classify("feature/readme", known) == ("default", None, None)
    assert classify("", known) == ("default", None, None)

    # On main, strings still to translate aren't failures, but a broken translation is.
    main = team["release"]
    assert bhasha("-C", main, "ci", "--head-ref", "feature/x", "--summary", tmp_path / "s.md") == 0
    assert "10 still to translate" in (tmp_path / "s.md").read_text()
    hi = main / "demo-app/locales/hi.json"
    data = json.loads(hi.read_text())
    data["pay.cta"] = "Pay {now}"
    hi.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert bhasha("-C", main, "ci", "--head-ref", "feature/x", "--summary", tmp_path / "s.md") == 1


def test_doctor_reports_data_and_remote_health(team, tmp_path, capsys):
    from bhasha import doctor

    root = team["scout"]
    results = {r["check"]: r["level"] for r in doctor.run(root)}
    assert results["origin"] == "ok" and results["languages.json"] == "ok" and results["glossary"] == "ok"
    assert "gh" not in results  # origin isn't GitHub

    sh(root, "remote", "set-url", "origin", str(tmp_path / "nowhere.git"))
    assert {r["check"]: r["level"] for r in doctor.run(root)}["origin"] == "fail"
    assert bhasha("-C", root, "doctor", "--offline") == 0

    langs = json.loads((root / "languages.json").read_text())
    langs["ta"]["script"] = "Tamill"
    (root / "languages.json").write_text(json.dumps(langs, ensure_ascii=False), encoding="utf-8")
    capsys.readouterr()
    assert bhasha("-C", root, "doctor", "--offline") == 1
    assert "no script table for ta (Tamill)" in capsys.readouterr().out


def test_releases_with_a_shared_prefix_stay_apart(team, tmp_path):
    scout, translator = team["scout"], team["translator"]
    for name in ("2.4-trial", "2.4"):
        sh(release.start(scout, name)["worktree"], "push", "-q", "-u", "origin", release.branch(name))
    trial_ta = Path(release.lang(translator, "2.4-trial", "ta")["worktree"])
    sh(trial_ta, "push", "-q", "-u", "origin", "l10n/r2.4-trial-ta")
    codes = {r["language"]: r["state"] for r in release.status(translator, "2.4")["languages"]}
    assert codes == {"hi": "not started", "ta": "not started"}  # not "trial-ta"
    assert bhasha("-C", translator, "demo", "clean", "2.4", "--remote") == 0
    assert release.status(translator, "2.4-trial")["languages"][1]["ref"] == "origin/l10n/r2.4-trial-ta"


def test_apply_refuses_unknown_keys_and_bad_json(team, tmp_path, capsys):
    root = team["translator"]
    assert bhasha("-C", root, "apply", "ta", write(tmp_path, "x.json", {"not.a.key": "x"})) == 2
    assert "aren't in en.json" in capsys.readouterr().err
    bad = tmp_path / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    assert bhasha("-C", root, "apply", "ta", bad) == 2


def test_new_language_is_added_to_the_app(team, tmp_path):
    root = team["translator"]
    assert bhasha("-C", root, "apply", "bn", write(tmp_path, "bn.json", {"nav.home": "হোম"})) == 1  # rest is missing
    index = json.loads((root / "demo-app/locales/index.json").read_text())
    assert index == ["en", "hi", "ta", "bn"]
