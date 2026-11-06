"""The release workflow, one git branch per job.

    main                 the app as it ships
    l10n/r<R>            the release integration branch; glossary decisions land here
    l10n/r<R>-<lang>     one language's translations, in its own worktree

The scout opens the release and is the only writer of the glossary, so there
is one canonical copy and no merge conflicts. Translators work on their
language branches and pick glossary decisions up with `sync`. The release
agent merges only languages whose approval still matches their strings, and
`verify` re-checks everything on the merged result, the same way CI does on
the pull request.

Records written along the way, all in git:

    releases/r<R>/RELEASE.json           who opened the release, when, from which commit
    releases/r<R>/approvals/<lang>.json  who approved, where, when, and digests of what they saw
    releases/r<R>/RELEASE_NOTES.md       the pull request body: approvals, decisions, timeline
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from bhasha import BhashaError, __version__, gitops
from bhasha.catalog import Catalog, Reader, write_json
from bhasha.checker import check, errors
from bhasha.glossary import load as load_glossary
from bhasha.glossary import rules_for
from bhasha.integrity import VALID, approval_state, rules_digest, short, source_digest, strings_digest

STAMP = "%Y-%m-%d %H:%M UTC"


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


def manifest_path(release: str) -> str:
    return f"releases/r{norm(release)}/RELEASE.json"


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def parse_time(text: Optional[str]) -> Optional[datetime]:
    """Read our own stamps ("2026-11-07 12:44 UTC") and git's ISO 8601 times."""
    if not text:
        return None
    try:
        return datetime.strptime(text, STAMP).replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _json_at(read: Reader, rel: str) -> Optional[dict]:
    text = read(rel)
    return json.loads(text) if text else None


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


def start(cwd: Path, release: str, by: Optional[str] = None) -> dict:
    """Open a release: create l10n/r<R> from main, in its own worktree, with its manifest."""
    gitops.fetch(cwd)
    name = branch(release)
    remote = gitops.ref_exists(cwd, f"origin/{name}")
    start_ref = f"origin/{name}" if remote else _start_point(cwd)
    path = ensure_worktree(cwd, name, start_ref)
    opened = None
    if not remote and not (path / manifest_path(release)).is_file():
        cat = Catalog.at(path)
        r = norm(release)
        record = {
            "release": r,
            "opened_by": (by or "").strip() or "bhasha-scout",
            "opened_at": now_utc().isoformat(),
            "base_commit": gitops.head(path),
            "new_strings": [k for k, m in cat.meta.items() if m.get("since") == r],
            "languages": cat.target_languages(),
            "tool": f"bhasha {__version__}",
        }
        write_json(path / manifest_path(release), record)
        opened = gitops.commit(path, [manifest_path(release)],
                               f"Open release {r}: {len(record['new_strings'])} new strings, opened by {record['opened_by']}")
    return {"release": norm(release), "branch": name, "worktree": str(path), "already_pushed": remote,
            "opened_commit": opened, "push": None if remote else f"git -C {path} push -u origin {name}"}


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
    before this language has synced it. An approval counts only while its
    digests still match the strings and the English.
    """
    base = Catalog(gitops.ref_reader(cwd, gitops.best_ref(cwd, branch(release)) or _start_point(cwd)))
    mine = Catalog(gitops.ref_reader(cwd, ref))
    info = base.language(code)
    translations = mine.locale(code)
    findings = check(base.english, base.meta, translations, info["script"], rules_for(base.read, code))
    approval = _json_at(mine.read, approval_path(release, code))
    return {
        "language": code,
        "name": info["name"],
        "ref": ref,
        "errors": [f.as_dict() for f in errors(findings)],
        "translated": sum(1 for k in base.english if k in translations),
        "total": len(base.english),
        "approval": approval,
        "approval_state": approval_state(approval, base.english, base.meta, translations),
    }


def language_branches(cwd: Path, release: str, known: set) -> list:
    """Language codes with a branch for this release (local or on origin).

    Only real language codes count, so release 2.4 never mistakes the branches
    of a release called 2.4-trial (l10n/r2.4-trial-ta) for its own.
    """
    prefix = branch(release) + "-"
    found = []
    for ref in gitops.git(cwd, "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin/l10n/", "refs/heads/l10n/").splitlines():
        name = ref[len("origin/"):] if ref.startswith("origin/") else ref
        code = name[len(prefix):] if name.startswith(prefix) else None
        if code in known and code not in found:
            found.append(code)
    return found


def languages_in_release(cwd: Path, release: str) -> list:
    base = Catalog(gitops.ref_reader(cwd, gitops.best_ref(cwd, branch(release)) or _start_point(cwd)))
    codes = base.target_languages()
    codes += [c for c in language_branches(cwd, release, set(base.languages)) if c not in codes]
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
        elif report["approval_state"] == VALID:
            report["state"] = "approved"
        elif report["approval"]:
            report["state"] = "approval stale"
        else:
            report["state"] = "in review"
        rows.append(report)
    return {"release": norm(release), "branch": branch(release), "languages": rows}


def approve(cwd: Path, release: str, code: str, by: str, room: Optional[str] = None) -> dict:
    """Record a reviewer's approval of exactly these strings. Refuses failing or unsynced work."""
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
    translations = cat.locale(code)
    rules = rules_for(cat.read, code)
    problems = errors(check(cat.english, cat.meta, translations, info["script"], rules))
    if problems:
        raise BhashaError(f"can't record an approval: {len(problems)} check(s) fail in {code}; run bhasha check {code}")
    record = {
        "release": norm(release),
        "language": code,
        "language_name": info["name"],
        "approved_by": by.strip(),
        "approved_in": (room or "").strip() or None,
        "approved_at": now_utc().strftime(STAMP),
        "approved_commit": gitops.head(root),
        "strings": len(translations),
        "checker": "pass",
        "strings_sha256": strings_digest(translations),
        "source_sha256": source_digest(cat.english, cat.meta),
        "glossary_sha256": rules_digest(rules),
        "tool": f"bhasha {__version__}",
    }
    write_json(root / approval_path(release, code), record)
    where = f" in {record['approved_in']}" if record["approved_in"] else ""
    sha = gitops.commit(root, [approval_path(release, code)],
                        f"Approve {info['name']} ({code}) for release {norm(release)}, approved by {by.strip()}{where}")
    return {**record, "commit": sha, "push": f"git -C {root} push -u origin {want}"}


def verify_tree(root: Path, release: str) -> dict:
    """Everything a release must satisfy, checked on one checkout.

    Runs on the merged release branch before the release agent pushes, and in
    CI on the pull request (where the checkout is the merge with main, so an
    English change that landed on main after an approval is caught too).
    Every language the app ships must have every string translated, pass every
    check, and carry an approval whose digests match.
    """
    cat = Catalog.at(Path(root))
    r = norm(release)
    manifest = _json_at(cat.read, manifest_path(r))
    rows = []
    for code in cat.target_languages():
        info = cat.language(code)
        translations = cat.locale(code)
        found = errors(check(cat.english, cat.meta, translations, info["script"], rules_for(cat.read, code)))
        approval = _json_at(cat.read, approval_path(r, code))
        problems = []
        missing = [f for f in found if f.rule == "missing"]
        if missing:
            problems.append(f"{len(missing)} strings not translated")
        if len(found) > len(missing):
            first = next(f for f in found if f.rule != "missing")
            problems.append(f"{len(found) - len(missing)} checks fail (first: {first.key}: {first.message})")
        state = approval_state(approval, cat.english, cat.meta, translations)
        if state is None:
            problems.append(f"no approval recorded for release {r}")
        elif state != VALID:
            problems.append(state)
        elif not str(approval.get("approved_by", "")).strip():
            problems.append("the approval names no reviewer")
        rows.append({
            "language": code,
            "name": info["name"],
            "translated": sum(1 for k in cat.english if k in translations),
            "total": len(cat.english),
            "strings_sha256": strings_digest(translations),
            "approval": approval,
            "problems": problems,
            "ok": not problems,
        })
    return {"release": r, "manifest": manifest, "languages": rows,
            "ok": bool(rows) and all(row["ok"] for row in rows)}


def language_history(cwd: Path, release: str, code: str, ref: str) -> dict:
    """When a language was first drafted, and how many revisions and syncs it took."""
    base = gitops.best_ref(cwd, branch(release))
    log = gitops.git(cwd, "log", "--reverse", "--format=%cI%x09%s", f"{base}..{ref}" if base else ref)
    drafts, syncs = [], 0
    for line in log.splitlines():
        when, _, subject = line.partition("\t")
        if subject.startswith(("Translate ", "Update ")) and subject.endswith(f"({code})"):
            drafts.append(parse_time(when))
        elif subject.startswith("Merge "):
            syncs += 1
    return {"drafted_at": drafts[0] if drafts else None, "revisions": len(drafts), "syncs": syncs}


def merge(cwd: Path, release: str) -> dict:
    """Merge every approved language into the release branch, verify it, and write the notes."""
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
        raise BhashaError(f"not shipping yet. Every language needs a current approval and passing checks ({detail})")
    if not approved:
        raise BhashaError("no languages in this release")

    history = {r["language"]: language_history(cwd, release, r["language"], r["ref"]) for r in approved}
    for r in approved:
        gitops.git(wt, "merge", "--no-edit", r["ref"], env=gitops.identity_env(wt))

    verified = verify_tree(wt, release)
    if not verified["ok"]:
        detail = "; ".join(f"{row['language']}: {', '.join(row['problems'])}" for row in verified["languages"] if not row["ok"])
        raise BhashaError(f"after merging, verification fails ({detail}); nothing was pushed")

    cat = Catalog.at(wt)
    notes = render_notes(cat, release, verified, history, now_utc())
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


def _clock(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%H:%M UTC")


def _duration(seconds: float) -> str:
    minutes = max(0, int(round(seconds / 60)))
    if minutes < 1:
        return "under a minute"
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    return f"{minutes // 60} h {minutes % 60:02d} min"


def _decisions(cat: Catalog, release: str, codes: list) -> list:
    """Glossary log entries made during this release, oldest first."""
    found = []
    for scope in ["common"] + codes:
        for entry in load_glossary(cat.read, scope).get("log", []):
            if entry.get("release") == release:
                found.append({**entry, "scope": scope})
    return sorted(found, key=lambda e: parse_time(e.get("at")) or datetime.max.replace(tzinfo=timezone.utc))


def render_notes(cat: Catalog, release: str, verified: dict, history: dict, shipped_at: datetime) -> str:
    r = norm(release)
    rows = verified["languages"]
    manifest = verified.get("manifest") or {}
    lines = [f"# Translations for release {r}", "",
             "Translated by Bhasha Desk agents, checked by the deterministic `bhasha check`, and approved in chat "
             "by a native-speaking reviewer for each language. Each approval is bound to a SHA-256 digest of the "
             f"exact strings the reviewer saw; CI re-verifies every one with `bhasha release verify {r}`.", "",
             "| Language | Strings | Approved by | In | Approved at | Strings digest |", "|---|---|---|---|---|---|"]
    for row in rows:
        a = row["approval"]
        lines.append(f"| {row['name']} ({row['language']}) | {row['translated']}/{row['total']} | {a['approved_by']} | "
                     f"{a.get('approved_in') or '-'} | {a['approved_at']} | `{short(a['strings_sha256'])}` |")

    decisions = _decisions(cat, r, [row["language"] for row in rows])
    lines += ["", "## Glossary decisions made during this release", ""]
    lines += [f"- {d['change']} ({'every language' if d['scope'] == 'common' else d['scope']}), decided by {d['by']} in {d['room']}"
              + (f": {d['note']}" if d.get("note") else "") for d in decisions] or ["- none"]

    # (time, stage, text). Some records keep minutes and some seconds, so events
    # are ordered by minute, then by stage: opened, drafted, decided, approved, shipped.
    events = []
    opened = parse_time(manifest.get("opened_at"))
    if opened:
        events.append((opened, 0, f"release {r} opened by {manifest.get('opened_by', 'bhasha-scout')}, "
                                  f"{len(manifest.get('new_strings', []))} new strings"))
    for row in rows:
        h = history.get(row["language"], {})
        if h.get("drafted_at"):
            events.append((h["drafted_at"], 1, f"{row['name']} drafts ready"))
    for d in decisions:
        when = parse_time(d.get("at"))
        if when:
            events.append((when, 2, f"glossary: {d['change']}, decided by {d['by']} in {d['room']}"))
    for row in rows:
        a, h = row["approval"], history.get(row["language"], {})
        when = parse_time(a.get("approved_at"))
        if when:
            extra = []
            if h.get("revisions"):
                extra.append(f"{h['revisions']} revision{'s' if h['revisions'] != 1 else ''}")
            if h.get("syncs"):
                extra.append(f"{h['syncs']} glossary sync{'s' if h['syncs'] != 1 else ''}")
            events.append((when, 3, f"{row['name']} approved by {a['approved_by']}" + (f" ({', '.join(extra)})" if extra else "")))
    events.append((shipped_at, 4, "merged, verified and shipped"))
    lines += ["", "## Timeline", ""]
    ordered_events = sorted(events, key=lambda e: (e[0].astimezone(timezone.utc).replace(second=0, microsecond=0), e[1], e[0]))
    lines += [f"- {_clock(when)} · {what}" for when, _, what in ordered_events]
    if opened:
        lines += ["", f"From release opened to pull request: **{_duration((shipped_at - opened).total_seconds())}**."]

    new_keys = manifest.get("new_strings") or [k for k, m in cat.meta.items() if m.get("since") == r]
    if new_keys:
        lines += ["", f"## New strings in {r}", ""] + [f"- `{k}`" for k in new_keys]
    lines += ["", f"Generated by `bhasha release merge` (bhasha {__version__})."]
    return "\n".join(lines) + "\n"
