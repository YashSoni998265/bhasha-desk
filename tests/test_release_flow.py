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

    # The scout opens the release and pushes the branch.
    started = release.start(scout, "2.4")
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
    sh(merged["worktree"], "push", "-q", "-u", "origin", "l10n/r2.4")


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
