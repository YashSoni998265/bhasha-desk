"""Preflight: can the agents work in this checkout?

The scout runs this when it starts, so a broken data file, an unreachable
remote or a missing `gh` login shows up as one line in chat at kickoff instead
of as a failed push halfway through a release. CI runs the data half
(`--offline`) on every push.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from bhasha import BhashaError, gitops
from bhasha.catalog import LOCALES, Catalog
from bhasha.glossary import KINDS
from bhasha.glossary import load as load_glossary
from bhasha.scripts import SCRIPT_RANGES

OK, WARN, FAIL = "ok", "warn", "fail"
LANGUAGE_FIELDS = ("name", "native", "script", "tone")


def _result(name: str, level: str, detail: str) -> dict:
    return {"check": name, "level": level, "detail": detail}


def _run(cmd: list, cwd: Path, timeout: int = 15):
    try:
        return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def data_checks(root: Path) -> list:
    cat = Catalog.at(root)
    results = []

    try:
        langs = cat.languages
    except BhashaError as exc:
        return [_result("languages.json", FAIL, str(exc))]
    incomplete = [c for c, v in langs.items() if not all(v.get(f) for f in LANGUAGE_FIELDS)]
    unknown = [f"{c} ({v.get('script')})" for c, v in langs.items() if v.get("script") not in SCRIPT_RANGES]
    if incomplete or unknown:
        detail = "; ".join(filter(None, [
            f"missing {'/'.join(LANGUAGE_FIELDS)} for {', '.join(incomplete)}" if incomplete else "",
            f"no script table for {', '.join(unknown)}" if unknown else "",
        ]))
        results.append(_result("languages.json", FAIL, detail))
    else:
        results.append(_result("languages.json", OK, f"{len(langs)} languages: {', '.join(langs)}"))

    try:
        english, meta = cat.english, cat.meta
    except BhashaError as exc:
        return results + [_result("strings", FAIL, str(exc))]
    if not english or not all(isinstance(v, str) and v.strip() for v in english.values()):
        results.append(_result("en.json", FAIL, "every English string must be non-empty text"))
    else:
        results.append(_result("en.json", OK, f"{len(english)} strings"))
    no_context = [k for k in english if not (meta.get(k) or {}).get("context")]
    bad_limit = [k for k, m in meta.items() if "max_len" in m and not (isinstance(m["max_len"], int) and m["max_len"] > 0)]
    orphans = [k for k in meta if k not in english]
    if bad_limit:
        results.append(_result("meta.json", FAIL, "max_len must be a positive whole number: " + ", ".join(bad_limit)))
    elif no_context or orphans:
        detail = "; ".join(filter(None, [
            f"no context for {', '.join(no_context)} (translators need it)" if no_context else "",
            f"describes strings that aren't in en.json: {', '.join(orphans)}" if orphans else "",
        ]))
        results.append(_result("meta.json", WARN, detail))
    else:
        results.append(_result("meta.json", OK, "every string has context"))

    index = cat.index
    problems = []
    if "en" not in index:
        problems.append("index.json must list en")
    for code in cat.target_languages():
        if code not in langs:
            problems.append(f"{code} is in index.json but not in languages.json")
        elif not cat.has_locale(code):
            problems.append(f"{code} is in index.json but {LOCALES}/{code}.json is missing")
    unlisted = sorted(p.stem for p in (root / LOCALES).glob("*.json")
                      if p.stem not in index and p.stem not in ("en", "meta", "index"))
    for code in cat.target_languages():
        if code in langs and cat.has_locale(code):
            try:
                extra = [k for k in cat.locale(code) if k not in english]
            except BhashaError as exc:
                problems.append(str(exc))
                continue
            if extra:
                problems.append(f"{code}.json has strings that aren't in en.json: {', '.join(extra)}")
    if problems:
        results.append(_result("locales", FAIL, "; ".join(problems)))
    elif unlisted:
        results.append(_result("locales", WARN, f"not in index.json, so the app never loads them: {', '.join(unlisted)}"))
    else:
        results.append(_result("locales", OK, "shipping " + ", ".join(index)))

    problems, count = [], 0
    for scope in ["common"] + [c for c in langs if (root / "glossary" / f"{c}.json").is_file()]:
        try:
            data = load_glossary(cat.read, scope)
        except json.JSONDecodeError as exc:
            problems.append(f"glossary/{scope}.json is not valid JSON: {exc}")
            continue
        for t in data.get("terms", []):
            count += 1
            if t.get("rule") not in KINDS:
                problems.append(f"{scope}: {t.get('term')!r} has unknown rule {t.get('rule')!r}")
            elif t["rule"] != "keep" and scope == "common":
                problems.append(f"common: a {t['rule']} rule for {t.get('term')!r} belongs to one language")
            elif t["rule"] != "keep" and not t.get("translation"):
                problems.append(f"{scope}: the {t['rule']} rule for {t.get('term')!r} needs a translation")
            if not t.get("decided_by"):
                problems.append(f"{scope}: {t.get('term')!r} doesn't say who decided it")
    results.append(_result("glossary", FAIL, "; ".join(problems)) if problems
                   else _result("glossary", OK, f"{count} rules, each with who decided it"))
    return results


def environment_checks(root: Path) -> list:
    results = [_result("python", OK, sys.version.split()[0])]
    if not gitops.is_repo(root):
        return results + [_result("git", WARN, "not a git checkout; releases and approvals need one")]
    if not gitops.has_remote(root):
        return results + [_result("origin", WARN, "no origin remote; agents can't share branches")]
    remote = gitops.git(root, "remote", "get-url", "origin")
    probe = _run(["git", "ls-remote", "--heads", "origin"], root)
    if probe.returncode != 0:
        why = (probe.stderr or probe.stdout).strip().splitlines()
        results.append(_result("origin", FAIL, f"can't reach {remote}: {why[-1] if why else 'no answer'}"))
    else:
        results.append(_result("origin", OK, f"{remote} is reachable"))
    if gitops.github_repo(root):
        auth = _run(["gh", "auth", "status"], root)
        if auth.returncode == 127:
            results.append(_result("gh", WARN, "GitHub CLI not installed; the release agent will post the notes instead of opening a pull request"))
        elif auth.returncode != 0:
            results.append(_result("gh", WARN, "GitHub CLI isn't logged in (run: gh auth login); no pull request until it is"))
        else:
            results.append(_result("gh", OK, "logged in, so the release agent can open the pull request"))
    who = gitops.identity_env(root)
    results.append(_result("identity", OK, f"commits are authored as {who['GIT_AUTHOR_NAME']} <{who['GIT_AUTHOR_EMAIL']}>"))
    return results


def run(root: Path, offline: bool = False) -> list:
    return data_checks(root) + ([] if offline else environment_checks(root))


def failed(results: list) -> bool:
    return any(r["level"] == FAIL for r in results)
