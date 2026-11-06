"""The release workflow, one git branch per job.

    main                 the app as it ships
    l10n/r<R>            the release integration branch; glossary decisions land here
    l10n/r<R>-<lang>     one language's translations, in its own worktree

The scout opens the release and is the only writer of the glossary, so there
is one canonical copy and no merge conflicts. Translators work on their
language branches and pick glossary decisions up with `sync`. The release
agent merges only languages with a recorded approval and a passing check.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from bhasha import BhashaError, gitops
from bhasha.catalog import Catalog, LOCALES, write_json
from bhasha.checker import check, errors
from bhasha.glossary import load as load_glossary
from bhasha.glossary import rules_for


def norm(release: str) -> str:
    r = release.strip().lstrip("rRvV")
    if not r or any(c.isspace() for c in r) or "/" in r:
        raise BhashaError(f"bad release name {release!r}; use something like 2.4")
    return r


def branch(release: str, lang: Optional[str] = None) -> str:
    return f"l10n/r{norm(release)}" + (f"-{lang}" if lang else "")


def approval_path(release: str, lang: str) -> str:
    return f"releases/r{norm(release)}/approvals/{lang}.json"


def notes_path(release: str) -> str:
    return f"releases/r{norm(release)}/RELEASE_NOTES.md"


def _start_point(cwd: Path) -> str:
    for ref in ("origin/main", "main"):
        if gitops.ref_exists(cwd, ref):
            return ref
    raise BhashaError("can't find main or origin/main")


def ensure_worktree(cwd: Path, name: str, start: str) -> Path:
    """Check out `name` in its own worktree next to the main checkout."""
    existing = gitops.worktree_for(cwd, name)
    if existing:
        return existing
    path = gitops.worktree_home(cwd) / ("wt-" + name.replace("l10n/", "").replace("/", "-"))
    if path.exists():
        raise BhashaError(f"{path} exists but isn't a worktree of {name}; move it away")
    if gitops.ref_exists(cwd, f"refs/heads/{name}"):
        gitops.git(cwd, "worktree", "add", str(path), name)
    else:
        gitops.git(cwd, "worktree", "add", "-b", name, str(path), start)
    return path


def start(cwd: Path, release: str) -> dict:
    """Open a release: create l10n/r<R> from main, in its own worktree."""
    gitops.fetch(cwd)
    name = branch(release)
    remote = gitops.ref_exists(cwd, f"origin/{name}")
    start_ref = f"origin/{name}" if remote else _start_point(cwd)
    path = ensure_worktree(cwd, name, start_ref)
    return {"release": norm(release), "branch": name, "worktree": str(path), "already_pushed": remote,
            "push": None if remote else f"git -C {path} push -u origin {name}"}


def lang(cwd: Path, release: str, code: str) -> dict:
    """Give one language its own worktree and branch, from the release branch."""
    gitops.fetch(cwd)
    rel_branch = branch(release)
    start_ref = gitops.best_ref(cwd, rel_branch)
    if start_ref is None:
        raise BhashaError(f"release branch {rel_branch} doesn't exist yet; ask the scout to start release {norm(release)}")
    Catalog(gitops.ref_reader(cwd, start_ref)).language(code)
    name = branch(release, code)
    remote = gitops.best_ref(cwd, name)
    path = ensure_worktree(cwd, name, remote or start_ref)
    return {"release": norm(release), "language": code, "branch": name, "worktree": str(path)}


def sync(cwd: Path, release: str, code: str) -> dict:
    """Bring the release branch's glossary decisions into a language branch."""
    gitops.fetch(cwd)
    path = gitops.worktree_for(cwd, branch(release, code))
    if path is None:
        raise BhashaError(f"no worktree for {branch(release, code)}; run: bhasha release lang {norm(release)} {code}")
    source = gitops.best_ref(cwd, branch(release))
    if source is None:
        raise BhashaError(f"{branch(release)} doesn't exist")
    before = gitops.head(path)
    gitops.git(path, "merge", "--no-edit", source, env=gitops.identity_env(path))
    return {"worktree": str(path), "merged": source, "before": before, "after": gitops.head(path)}


def language_report(cwd: Path, ref: str, code: str, release: str) -> dict:
    """Judge a language branch against the release branch's current English and glossary.

    The release branch is the one canonical copy of the glossary, so a decision
    made for another language counts here the moment it is published, even
    before this language has synced it.
    """
    base = Catalog(gitops.ref_reader(cwd, gitops.best_ref(cwd, branch(release)) or _start_point(cwd)))
    mine = Catalog(gitops.ref_reader(cwd, ref))
    info = base.language(code)
    translations = mine.locale(code)
    findings = check(base.english, base.meta, translations, info["script"], rules_for(base.read, code))
    approval_text = gitops.show(cwd, ref, approval_path(release, code))
    approval = json.loads(approval_text) if approval_text else None
    return {
        "language": code,
        "name": info["name"],
        "ref": ref,
        "errors": [f.as_dict() for f in errors(findings)],
        "translated": sum(1 for k in base.english if k in translations),
        "total": len(base.english),
        "approval": approval,
    }


def languages_in_release(cwd: Path, release: str) -> list:
    codes = []
    base = gitops.best_ref(cwd, branch(release)) or _start_point(cwd)
    index = gitops.show(cwd, base, f"{LOCALES}/index.json")
    if index:
        codes = [c for c in json.loads(index) if c != "en"]
    prefix = branch(release) + "-"
    for ref in gitops.git(cwd, "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin/l10n/", "refs/heads/l10n/").splitlines():
        name = ref[len("origin/"):] if ref.startswith("origin/") else ref
        if name.startswith(prefix):
            code = name[len(prefix):]
            if code not in codes:
                codes.append(code)
    return codes


def status(cwd: Path, release: str) -> dict:
    gitops.fetch(cwd)
    rows = []
    for code in languages_in_release(cwd, release):
        ref = gitops.best_ref(cwd, branch(release, code))
        if ref is None:
            rows.append({"language": code, "state": "not started"})
            continue
        report = language_report(cwd, ref, code, release)
        if report["errors"]:
            report["state"] = "failing checks"
        elif report["approval"]:
            report["state"] = "approved"
        else:
            report["state"] = "in review"
        rows.append(report)
    return {"release": norm(release), "branch": branch(release), "languages": rows}


def approve(cwd: Path, release: str, code: str, by: str) -> dict:
    """Record a reviewer's approval in the language branch. Refuses failing work."""
    if not by.strip():
        raise BhashaError("say who approved with --by")
    root = Path(cwd)
    want = branch(release, code)
    if gitops.current_branch(root) != want:
        path = gitops.worktree_for(root, want)
        if path is None:
            raise BhashaError(f"run this in the {want} worktree (bhasha release lang {norm(release)} {code})")
        root = path
    gitops.fetch(root)
    published = gitops.best_ref(root, branch(release))
    if published and not gitops.ok(root, "merge-base", "--is-ancestor", published, "HEAD"):
        raise BhashaError(f"{branch(release)} has decisions this branch hasn't picked up yet; "
                          f"run: bhasha release sync {norm(release)} {code}")
    cat = Catalog.at(root)
    info = cat.language(code)
    problems = errors(check(cat.english, cat.meta, cat.locale(code), info["script"], rules_for(cat.read, code)))
    if problems:
        raise BhashaError(f"can't record an approval: {len(problems)} check(s) fail in {code}; run bhasha check {code}")
    record = {
        "release": norm(release),
        "language": code,
        "language_name": info["name"],
        "approved_by": by.strip(),
        "approved_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "approved_commit": gitops.head(root),
        "strings": len(cat.locale(code)),
        "checker": "pass",
    }
    write_json(root / approval_path(release, code), record)
    sha = gitops.commit(root, [approval_path(release, code)], f"Approve {info['name']} ({code}) for release {norm(release)}, approved by {by.strip()}")
    return {**record, "commit": sha, "push": f"git -C {root} push -u origin {want}"}


def merge(cwd: Path, release: str) -> dict:
    """Merge every approved language into the release branch and write the notes."""
    gitops.fetch(cwd)
    rel = branch(release)
    if gitops.best_ref(cwd, rel) is None:
        raise BhashaError(f"{rel} doesn't exist")
    wt = ensure_worktree(cwd, rel, gitops.best_ref(cwd, rel))
    if gitops.ref_exists(wt, f"origin/{rel}"):
        gitops.git(wt, "merge", "--ff-only", f"origin/{rel}")

    current = status(cwd, release)
    approved = [r for r in current["languages"] if r.get("state") == "approved"]
    blocked = [r for r in current["languages"] if r.get("state") != "approved"]
    if blocked:
        detail = "; ".join(f"{r['language']}: {r['state']}" for r in blocked)
        raise BhashaError(f"not shipping yet. Every language needs an approval and passing checks ({detail})")
    if not approved:
        raise BhashaError("no languages in this release")

    for r in approved:
        gitops.git(wt, "merge", "--no-edit", r["ref"], env=gitops.identity_env(wt))

    cat = Catalog.at(wt)
    final = {}
    for r in approved:
        info = cat.language(r["language"])
        final[r["language"]] = errors(check(cat.english, cat.meta, cat.locale(r["language"]), info["script"], rules_for(cat.read, r["language"])))
    broken = {k: v for k, v in final.items() if v}
    if broken:
        raise BhashaError("after merging, checks fail in " + ", ".join(broken) + "; nothing was pushed")

    notes = render_notes(cat, release, approved)
    (wt / notes_path(release)).parent.mkdir(parents=True, exist_ok=True)
    (wt / notes_path(release)).write_text(notes, encoding="utf-8")
    sha = gitops.commit(wt, [notes_path(release)], f"Release notes for translations in release {norm(release)}")
    repo = gitops.github_repo(wt)
    pr = None
    if repo:
        pr = (f"gh pr create --repo {repo} --base main --head {rel} "
              f"--title \"Translations for release {norm(release)}\" --body-file {wt / notes_path(release)}")
    return {
        "release": norm(release),
        "worktree": str(wt),
        "languages": [r["language"] for r in approved],
        "commit": sha,
        "notes": str(wt / notes_path(release)),
        "push": f"git -C {wt} push -u origin {rel}",
        "pr": pr,
    }


def render_notes(cat: Catalog, release: str, approved: list) -> str:
    r = norm(release)
    lines = [f"# Translations for release {r}", "",
             "Translated by Bhasha Desk agents, checked by the deterministic `bhasha check`, "
             "and approved in chat by a native-speaking reviewer for each language.", "",
             "| Language | Strings | Approved by | Approved at | Checker |", "|---|---|---|---|---|"]
    for row in approved:
        a = row["approval"]
        lines.append(f"| {row['name']} ({row['language']}) | {row['translated']}/{row['total']} | {a['approved_by']} | {a['approved_at']} | pass |")
    decisions = []
    for scope in ["common"] + [row["language"] for row in approved]:
        for entry in load_glossary(cat.read, scope).get("log", []):
            if entry.get("release") == r:
                where = "every language" if scope == "common" else scope
                decisions.append(f"- {entry['change']} ({where}), decided by {entry['by']} in {entry['room']}" + (f": {entry['note']}" if entry.get("note") else ""))
    lines += ["", "## Glossary decisions made during this release", ""]
    lines += decisions or ["- none"]
    new_keys = [k for k, m in cat.meta.items() if m.get("since") == r]
    if new_keys:
        lines += ["", f"## New strings in {r}", ""] + [f"- `{k}`" for k in new_keys]
    lines += ["", "Generated by `bhasha release merge`."]
    return "\n".join(lines) + "\n"
