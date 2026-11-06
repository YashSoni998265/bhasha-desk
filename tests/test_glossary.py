import json

import pytest

from bhasha import BhashaError
from bhasha.catalog import file_reader
from bhasha.glossary import add_rule, rules_for


def test_add_keeps_current_truth_and_an_append_only_log(tmp_path):
    (tmp_path / "glossary").mkdir()
    add_rule(tmp_path, "common", "AutoPay", "keep", "Arjun", "bhasha-ta", release="2.4", now="t1")
    add_rule(tmp_path, "common", "AutoPay", "keep", "Priya", "bhasha-hi", release="2.4", now="t2")
    data = json.loads((tmp_path / "glossary" / "common.json").read_text())
    assert [t["decided_by"] for t in data["terms"]] == ["Priya"]  # superseded, not duplicated
    assert [e["by"] for e in data["log"]] == ["Arjun", "Priya"]  # history kept
    assert data["log"][1]["change"].startswith("replace ")


def test_rules_for_merges_common_and_language(tmp_path):
    (tmp_path / "glossary").mkdir()
    add_rule(tmp_path, "common", "UPI", "keep", "brand", "seed")
    add_rule(tmp_path, "ta", "mandate", "prefer", "Arjun", "bhasha-ta", translation="ஆணை")
    rules = rules_for(file_reader(tmp_path), "ta")
    assert [(r.scope, r.rule, r.term) for r in rules] == [("common", "keep", "UPI"), ("ta", "prefer", "mandate")]
    assert rules_for(file_reader(tmp_path), "hi")[0].term == "UPI"


@pytest.mark.parametrize("kwargs, message", [
    ({"scope": "ta", "rule": "prefer"}, "needs --translation"),
    ({"scope": "common", "rule": "prefer", "translation": "x"}, "about one language"),
    ({"scope": "ta", "rule": "keep", "decided_by": " "}, "who decided"),
    ({"scope": "ta", "rule": "shout"}, "must be one of"),
])
def test_bad_rules_are_refused(tmp_path, kwargs, message):
    args = {"term": "mandate", "decided_by": "Arjun", "room": "bhasha-ta", **kwargs}
    with pytest.raises(BhashaError, match=message):
        add_rule(tmp_path, **args)
