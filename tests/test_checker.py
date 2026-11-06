"""Every rule is tested in both directions: it passes good text and fails bad text."""

from bhasha.checker import check, errors
from bhasha.glossary import Rule
from bhasha.scripts import visual_length

EN = {
    "pay.cta": "Pay now",
    "pay.success": "Paid ₹{amount} to {payee}",
    "cancel": "Cancelling stops payments to <b>{merchant}</b>.",
    "title": "UPI AutoPay",
    "mandate": "Approve the mandate",
}
META = {"pay.cta": {"max_len": 10}}


def keep(term, by="Arjun", room="bhasha-ta"):
    return Rule(term, "keep", None, "", by, room, "2026-11-06", "2.4")


def rules_of(found, key=None):
    return sorted(f.rule for f in found if key is None or f.key == key)


def test_good_hindi_passes_every_rule():
    hi = {
        "pay.cta": "अभी पे करें",
        "pay.success": "{payee} को ₹{amount} का पेमेंट हो गया",
        "cancel": "रद्द करने से <b>{merchant}</b> को आगे के पेमेंट रुक जाएंगे।",
        "title": "UPI AutoPay",
        "mandate": "मैंडेट मंज़ूर करें",
    }
    assert errors(check(EN, META, hi, "Devanagari", [keep("UPI"), keep("AutoPay")])) == []


def test_missing_and_empty():
    found = check(EN, META, {"pay.cta": "  "}, "Devanagari")
    assert rules_of(found, "pay.cta") == ["empty"]
    assert rules_of(found, "pay.success") == ["missing"]


def test_untranslated_english_fails_but_keep_only_strings_pass():
    found = check({"a": "Pay now", "b": "UPI"}, {}, {"a": "Pay now", "b": "UPI"}, "Tamil", [keep("UPI")])
    assert rules_of(found, "a") == ["untranslated"]
    assert rules_of(found, "b") == []


def test_placeholder_missing_or_invented():
    found = check(EN, {}, {"pay.success": "{payee}-க்கு ₹{amt} செலுத்தப்பட்டது"}, "Tamil", keys=["pay.success"])
    messages = sorted(f.message for f in found)
    assert messages == ["placeholder {amount} is missing", "placeholder {amt} isn't in the English"]


def test_tags_must_match():
    bad = {"cancel": "रद्द करने से {merchant} को आगे के पेमेंट रुक जाएंगे।"}
    assert rules_of(check(EN, {}, bad, "Devanagari", keys=["cancel"])) == ["tags"]


def test_length_budget_uses_visual_length():
    assert visual_length("அமை") == 2  # the vowel sign joins the letter before it
    long = {"pay.cta": "இப்போதே பணம் செலுத்துங்கள்"}
    found = check(EN, META, long, "Tamil", keys=["pay.cta"])
    assert rules_of(found) == ["length"]
    short = {"pay.cta": "செலுத்து"}
    assert check(EN, META, short, "Tamil", keys=["pay.cta"]) == []


def test_english_left_in_translation():
    found = check(EN, {}, {"mandate": "Mandate मंज़ूर करें"}, "Devanagari", keys=["mandate"])
    assert rules_of(found) == ["english-left"]
    assert "Mandate" in found[0].message


def test_wrong_script():
    found = check(EN, {}, {"mandate": "ஆணையை அனுமதி করুন"}, "Tamil", keys=["mandate"])
    assert rules_of(found) == ["script"]
    assert "Bengali" in found[0].message


def test_keep_rule_flags_a_translated_brand_term_and_says_who_decided():
    translit = {"title": "UPI ஆட்டோபே"}
    found = check(EN, {}, translit, "Tamil", [keep("UPI"), keep("AutoPay")], keys=["title"])
    assert rules_of(found) == ["glossary"]
    assert 'keep "AutoPay" in English (decided by Arjun in bhasha-ta)' in found[0].message
    assert check(EN, {}, {"title": "UPI AutoPay"}, "Tamil", [keep("UPI"), keep("AutoPay")], keys=["title"]) == []


def test_without_a_keep_rule_latin_brand_terms_count_as_english():
    found = check(EN, {}, {"title": "UPI AutoPay வசதி"}, "Tamil", [keep("UPI")], keys=["title"])
    assert rules_of(found) == ["english-left"]


def test_prefer_and_avoid_rules():
    prefer = Rule("mandate", "prefer", "ஆணை", "", "Arjun", "bhasha-ta", "now", "2.4", "ta")
    avoid = Rule("mandate", "avoid", "கட்டளை", "", "Arjun", "bhasha-ta", "now", "2.4", "ta")
    bad = {"mandate": "கட்டளையை அனுமதிக்கவும்"}
    found = check(EN, {}, bad, "Tamil", [prefer, avoid], keys=["mandate"])
    assert rules_of(found) == ["glossary", "glossary"]
    good = {"mandate": "ஆணையை அனுமதிக்கவும்"}
    assert check(EN, {}, good, "Tamil", [prefer, avoid], keys=["mandate"]) == []


def test_unknown_keys_are_warnings_not_errors():
    found = check({"a": "Pay"}, {}, {"a": "செலுத்து", "zz": "x"}, "Tamil")
    assert [(f.key, f.severity) for f in found] == [("zz", "warning")]
    assert errors(found) == []


def test_the_same_english_translated_two_ways_is_a_warning():
    en = {"a.pause": "Pause", "b.pause": "Pause", "c.pay": "Pay now"}
    same = check(en, {}, {"a.pause": "रोकें", "b.pause": "रोकें", "c.pay": "अभी पे करें"}, "Devanagari")
    assert [f for f in same if f.rule == "consistency"] == []
    differ = check(en, {}, {"a.pause": "रोकें", "b.pause": "ठहरें", "c.pay": "अभी पे करें"}, "Devanagari")
    flagged = [f for f in differ if f.rule == "consistency"]
    assert [(f.key, f.severity) for f in flagged] == [("b.pause", "warning")]
    assert errors(differ) == []
