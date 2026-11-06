"""The no-Switch replay runs end to end and shows every guarantee firing."""

from bhasha.checker import check, errors
from bhasha.cli import main
from bhasha.demo_data import TAMIL_DRAFT, TAMIL_KEEP_AUTOPAY
from bhasha.glossary import Rule


def test_the_replay_catches_the_rule_the_stale_approval_and_the_tampering(tmp_path, capsys):
    assert main(["demo", "run", "--keep", str(tmp_path / "replay")]) == 0
    out = capsys.readouterr().out
    assert "sync → ❌ 2 strings break the new rule" in out
    assert "refused: not shipping yet   ← the gate holding" in out
    assert "hi: approval stale" in out
    assert "l10n gate on a clean clone → ✅ every language is translated, checked and approved" in out
    assert "❌ strings changed after the approval   ← caught" in out
    assert "4 strings that broke the new rule (2 Tamil, 2 Hindi)" in out
    notes = next((tmp_path / "replay").glob("agents/release/wt-r2.4/releases/r2.4/RELEASE_NOTES.md"))
    assert "Hindi approved by Priya (3 revisions, 1 glossary sync)" in notes.read_text(encoding="utf-8")


def test_a_reviewers_rule_must_be_recorded_before_the_strings_change():
    """Why the translator waits for the sync: until "keep AutoPay" is in the glossary,
    AutoPay in Latin letters is English left in a Tamil string."""
    en = {k: k for k in TAMIL_DRAFT}
    en["autopay.setup_cta"] = "Set up AutoPay"
    fixed = {**TAMIL_DRAFT, **TAMIL_KEEP_AUTOPAY}
    before = errors(check(en, {}, {"autopay.setup_cta": fixed["autopay.setup_cta"]}, "Tamil", keys=["autopay.setup_cta"]))
    assert [f.rule for f in before] == ["english-left"]
    keep = Rule("AutoPay", "keep", None, "", "Arjun", "bhasha-ta", "2026-11-07", "2.4")
    after = errors(check(en, {}, {"autopay.setup_cta": fixed["autopay.setup_cta"]}, "Tamil", [keep], keys=["autopay.setup_cta"]))
    assert after == []
