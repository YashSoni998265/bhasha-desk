"""The Switch template, linted the way Switch lints it, plus our own conventions.

Switch's linter warns about placeholders no parameter declares (they're left
in the document as written). On top of that: every name a room or link uses
exists, every agent waits for a person's approval, the release agent answers
only its owner, prompts never address an agent by accident, and every
`bhasha` command a prompt tells an agent to run really exists.
"""

import argparse
import re
from pathlib import Path

import pytest
import yaml

from bhasha.cli import build_parser

TEMPLATE = Path(__file__).resolve().parents[1] / "switch" / "bhasha-desk.template.yaml"
BUILTINS = {"$creator", "$creator_email", "$date", "$timestamp", "$agents_dir"}
PLACEHOLDER = re.compile(r"\{(\$?[A-Za-z_][A-Za-z0-9_]*)\}")


@pytest.fixture(scope="module")
def doc():
    return yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))


def strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from strings(k)
            yield from strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from strings(v)


def test_every_placeholder_is_declared(doc):
    declared = set(doc["params"]) | BUILTINS
    used = {name for s in strings({k: v for k, v in doc.items() if k != "params"}) for name in PLACEHOLDER.findall(s)}
    assert used - declared == set(), "Switch leaves undeclared placeholders in the document as written"
    assert {"team", "repo", "bridge"} <= used


def test_agents_are_supervised_and_scoped(doc):
    agents = {a["name"]: a for a in doc["agents"]}
    assert len(agents) == len(doc["agents"]) == 3
    for name, a in agents.items():
        assert name.startswith("{team}-")
        assert a["auto_approve"] is False, f"{name} must ask a person before acting"
        assert a["addressing"] in ("owner", "owner-agents", "anyone")
        assert a["provider"] and a["repo"] == "{repo}"
    assert agents["{team}-release"]["addressing"] == "owner"


def test_rooms_links_aliases_and_kickoffs_line_up(doc):
    agents = {a["name"] for a in doc["agents"]}
    rooms = {r["name"]: r for r in doc["rooms"]}
    for name, room in rooms.items():
        assert name.startswith("{team}-")
        assert set(room["agents"]) <= agents, name
        aliases = room.get("aliases") or {}
        assert set(aliases) <= set(room["agents"]), name
        assert not set(aliases.values()) & agents
        if room.get("kickoff"):
            addressed = re.match(r"@(\S+)", room["kickoff"].strip())
            assert addressed and addressed.group(1) in room["agents"], f"{name}'s kickoff must address an agent in it"
    for link in doc["links"]:
        assert link["from"] in rooms and link["to"] in rooms and link["label"]
    roles = [(r["name"], role) for r in doc["rooms"] for role in r.get("roles", [])]
    assert [(room, role["name"], role["exclusive"]) for room, role in roles] == [("{team}-ship", "release-captain", True)]


def test_language_rooms_are_registered_on_the_hub(doc):
    """The hub's links are the language registry: the scout briefs every room linked as 'language: ...'."""
    labels = {link["to"]: link["label"] for link in doc["links"] if link["from"] == "{team}-hub"}
    language_rooms = [r["name"] for r in doc["rooms"] if r["name"] not in ("{team}-hub", "{team}-ship")]
    for room in language_rooms:
        assert labels[room].startswith("language: "), room
    assert not labels["{team}-ship"].startswith("language: ")


def test_agents_belong_to_every_room_their_instructions_send_them_to(doc):
    """Switch's connect_to_room only works for members, so the round trips need memberships."""
    rooms = {r["name"]: set(r["agents"]) for r in doc["rooms"]}
    language_rooms = [n for n in rooms if n not in ("{team}-hub", "{team}-ship")]
    assert "{team}-translator" in rooms["{team}-hub"], "the translator brings reviewers' rules to the scout in the hub"
    for name in language_rooms:
        assert {"{team}-scout", "{team}-translator"} <= rooms[name], f"the scout briefs and syncs {name}"
    assert rooms["{team}-ship"] == {"{team}-release"}, "only the release agent works in the release room"


def test_prompts_never_address_an_agent_by_accident(doc):
    for a in doc["agents"]:
        assert not re.search(r"@[\w{]", a["instructions"]), f"a literal @name in {a['name']}'s instructions would address that agent"


def _choices(parser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    return {}


def _options(parser):
    return {opt for action in parser._actions for opt in action.option_strings}


def test_every_bhasha_command_in_a_prompt_exists(doc):
    root = build_parser()
    commands = [c for s in strings(doc) for c in re.findall(r"`uv run bhasha ([^`]+)`", s)]
    assert len(commands) >= 10
    for command in commands:
        parser, words = root, command.replace("...", " ").split()
        while words and _choices(parser).get(words[0]):
            parser = _choices(parser)[words.pop(0)]
        assert parser is not root, command
        for word in words:
            if word.startswith("--"):
                assert word.split("=")[0] in _options(parser), f"{command}: {word} isn't an option"
