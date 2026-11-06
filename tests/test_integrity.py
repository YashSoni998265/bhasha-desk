"""Approvals are bound to the exact strings and English the reviewer saw."""

from bhasha.integrity import VALID, approval_state, digest, short, source_digest, strings_digest

EN = {"pay.cta": "Pay now"}
META = {"pay.cta": {"context": "Button.", "max_len": 18}}
TA = {"pay.cta": "இப்போது செலுத்தவும்"}


def approval(**over):
    record = {"approved_by": "Arjun", "strings_sha256": strings_digest(TA), "source_sha256": source_digest(EN, META)}
    record.update(over)
    return record


def test_digest_ignores_key_order_and_layout_but_not_content():
    assert digest({"a": 1, "b": "x"}) == digest({"b": "x", "a": 1})
    assert digest({"a": "செலுத்து"}) != digest({"a": "செலுத்து "})
    assert digest({}).startswith("sha256:") and len(short(digest({}))) == 12


def test_an_approval_counts_while_nothing_changed():
    assert approval_state(approval(), EN, META, TA) == VALID
    assert approval_state(None, EN, META, TA) is None


def test_changing_a_string_voids_the_approval():
    edited = {"pay.cta": "இப்போதே செலுத்தவும்"}
    assert approval_state(approval(), EN, META, edited) == "strings changed after the approval"


def test_changing_the_english_or_its_limits_voids_the_approval():
    assert approval_state(approval(), {"pay.cta": "Pay"}, META, TA) == "the English changed after the approval"
    tighter = {"pay.cta": {"context": "Button.", "max_len": 10}}
    assert approval_state(approval(), EN, tighter, TA) == "the English changed after the approval"


def test_an_approval_without_digests_cannot_be_verified():
    assert "can't be verified" in approval_state({"approved_by": "Arjun"}, EN, META, TA)
