"""bhasha: the command-line tool the agents (and people) run.

Output is written to be pasted into a chat: one line per item, no tables,
because Slack and Telegram don't render Markdown tables. Commands that CI runs
can also write a Markdown summary (--summary) for the GitHub job page.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
import sys
from pathlib import Path
from typing import Optional

from bhasha import BhashaError, __version__, ci, doctor, gitops, release, report
from bhasha.catalog import Catalog, LOCALES, find_root, ordered, write_json
from bhasha.checker import check, errors
from bhasha.glossary import add_rule, load as load_glossary, rules_for
from bhasha.integrity import VALID, approval_state

OK, FAIL, WAIT, WARN = report.OK, report.FAIL, report.WAIT, report.WARN


def out(args, data, text: str) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str) if getattr(args, "json", False) else text)


def write_summary(path, markdown: str) -> None:
    """Append Markdown to a file, e.g. $GITHUB_STEP_SUMMARY."""
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(markdown.rstrip() + "\n\n")


def lang_label(cat: Catalog, code: str) -> str:
    info = cat.language(code)
    return f"{info['name']} ({code})"


def findings_for(cat: Catalog, code: str, keys=None) -> list:
    info = cat.language(code)
    return check(cat.english, cat.meta, cat.locale(code), info["script"], rules_for(cat.read, code), keys)


# ── status / brief / check ───────────────────────────────────────────────


def cmd_status(args) -> int:
    root = find_root(args.C)
    cat = Catalog.at(root)
    eng = cat.english
    rows, lines = [], [f"PaySathi strings · {len(eng)} in English"]
    for code in cat.target_languages():
        loc = cat.locale(code)
        found = findings_for(cat, code)
        missing = [f for f in found if f.rule == "missing"]
        other = [f for f in errors(found) if f.rule != "missing"]
        done = sum(1 for k in eng if k in loc)
        rows.append({"language": code, "translated": done, "total": len(eng), "missing": len(missing), "problems": len(other)})
        mark = OK if not errors(found) else (WAIT if not other else FAIL)
        lines.append(f"{mark} {lang_label(cat, code)}: {done}/{len(eng)} translated, {len(missing)} to translate, {len(other)} other problems")
    out(args, {"languages": rows}, "\n".join(lines))
    return 0


def cmd_brief(args) -> int:
    root = find_root(args.C)
    cat = Catalog.at(root)
    info = cat.language(args.lang)
    eng, meta, loc = cat.english, cat.meta, cat.locale(args.lang)
    found = findings_for(cat, args.lang)
    todo = [k for k in eng if any(f.key == k and f.severity == "error" for f in found)]
    rules = rules_for(cat.read, args.lang)
    items = []
    for k in todo:
        m = meta.get(k, {})
        items.append({
            "key": k,
            "english": eng[k],
            "current": loc.get(k),
            "context": m.get("context", ""),
            "max_len": m.get("max_len"),
            "new_in": m.get("since"),
            "problems": [f.message for f in found if f.key == k and f.rule != "missing"],
        })
    data = {
        "language": args.lang,
        "name": info["name"],
        "native": info["native"],
        "script": info["script"],
        "tone": info["tone"],
        "glossary": [{"rule": r.describe(), "provenance": r.provenance()} for r in rules],
        "strings": items,
    }
    lines = [f"Brief for {info['name']} ({args.lang}, {info['native']}) · script: {info['script']} · {len(items)} strings need work",
             f"Tone: {info['tone']}",
             "Glossary: " + ("; ".join(f"{r.describe()} ({r.provenance()})" for r in rules) or "none")]
    for it in items:
        limit = f" (max {it['max_len']} characters)" if it["max_len"] else ""
        lines.append(f"• {it['key']}{limit}: \"{it['english']}\". {it['context']}")
        if it["current"] is not None:
            lines.append(f"    now: \"{it['current']}\". Problems: {'; '.join(it['problems'])}")
    if items:
        lines.append(f"Write {{\"key\": \"translation\", ...}} to a JSON file, then run: uv run bhasha apply {args.lang} <file>")
    else:
        lines.append(f"{OK} Nothing to do: every string passes.")
    out(args, data, "\n".join(lines))
    return 0


def render_findings(cat: Catalog, code: str, found: list) -> str:
    errs, warns = errors(found), [f for f in found if f.severity != "error"]
    head = f"check · {lang_label(cat, code)} · {len(cat.english)} strings · "
    if not errs:
        head += f"{OK} all checks pass" + (f" ({len(warns)} warnings)" if warns else "")
    else:
        head += f"{FAIL} {len(errs)} problem{'s' if len(errs) != 1 else ''}"
    body = [f"• {f.key}: {f.message}" + (" (warning)" if f.severity != "error" else "") for f in found]
    return "\n".join([head, *body])


def cmd_check(args) -> int:
    root = find_root(args.C)
    cat = Catalog.at(root)
    codes = args.langs or cat.target_languages()
    failed, blocks, data = False, [], {}
    for code in codes:
        found = findings_for(cat, code)
        if args.allow_missing:
            found = [f for f in found if f.rule != "missing"]
        failed = failed or bool(errors(found))
        data[code] = [f.as_dict() for f in found]
        blocks.append(render_findings(cat, code, found))
    out(args, data, "\n\n".join(blocks))
    return 1 if failed else 0


# ── apply ────────────────────────────────────────────────────────────────


def _release_of_branch(root: Path, code: str):
    """The release whose language branch is checked out here, if any."""
    if not gitops.is_repo(root):
        return None
    name = gitops.current_branch(root)
    suffix = f"-{code}"
    if name.startswith("l10n/r") and name.endswith(suffix) and len(name) > len("l10n/r") + len(suffix):
        return name[len("l10n/r"):-len(suffix)]
    return None


def apply_translations(root: Path, code: str, incoming: dict, commit: bool = True) -> dict:
    """Write translations for one language, commit them, and check the result.

    Returns what changed, the commit, the findings, and whether an approval on
    this language branch no longer matches (so the reviewer must approve again).
    """
    cat = Catalog.at(root)
    info = cat.language(code)
    if not isinstance(incoming, dict) or not all(isinstance(v, str) for v in incoming.values()):
        raise BhashaError('expected a JSON object of {"key": "translation"}')
    eng = cat.english
    unknown = [k for k in incoming if k not in eng]
    if unknown:
        raise BhashaError("these keys aren't in en.json: " + ", ".join(unknown))

    created = not cat.has_locale(code)
    current = cat.locale(code)
    changed = [k for k, v in incoming.items() if current.get(k) != v]
    current.update(incoming)
    path = root / LOCALES / f"{code}.json"
    write_json(path, ordered(eng, current))
    paths = [path.relative_to(root)]
    if created:
        index = cat.index
        if code not in index:
            index.append(code)
            write_json(root / LOCALES / "index.json", index)
            paths.append(Path(LOCALES) / "index.json")

    sha = None
    if commit and changed:
        verb = "Translate" if created or len(changed) > 1 else "Update"
        sha = gitops.commit(root, paths, f"{verb} {len(changed)} {info['name']} string{'s' if len(changed) != 1 else ''} ({code})")
    after = Catalog.at(root)
    found = findings_for(after, code)

    voided_by = None
    rel = _release_of_branch(root, code)
    if rel and changed:
        approval_file = root / release.approval_path(rel, code)
        if approval_file.is_file():
            approval = json.loads(approval_file.read_text(encoding="utf-8"))
            if approval_state(approval, after.english, after.meta, after.locale(code)) != VALID:
                voided_by = approval.get("approved_by", "The reviewer")
    return {"language": code, "name": info["name"], "changed": changed, "commit": sha, "findings": found,
            "catalog": after, "approval_voided_by": voided_by}


def cmd_apply(args) -> int:
    root = find_root(args.C)
    raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    try:
        incoming = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BhashaError(f"{args.file} is not valid JSON: {exc}") from exc
    res = apply_translations(root, args.lang, incoming, commit=not args.no_commit)
    cat = res["catalog"]
    n = len(res["changed"])
    print(f"Applied {n} change{'s' if n != 1 else ''} to {lang_label(cat, args.lang)}" + (f" in commit {res['commit']}" if res["commit"] else ""))
    print(render_findings(cat, args.lang, res["findings"]))
    if res["approval_voided_by"]:
        print(f"{WARN} {res['approval_voided_by']} approved the earlier strings, so that approval "
              f"no longer counts. Show the changed lines and ask for approval again.")
    return 1 if errors(res["findings"]) else 0


# ── glossary ─────────────────────────────────────────────────────────────


def record_decision(start_dir: Path, scope: str, term: str, rule_kind: str, decided_by: str, room: str,
                    rel: Optional[str] = None, translation: Optional[str] = None, note: str = "") -> dict:
    """Record a reviewer's glossary decision, on the release branch when a release is given."""
    target = find_root(start_dir)
    push = None
    if rel:
        info = release.start(start_dir, rel)
        target = Path(info["worktree"])
        push = f"git -C {target} push origin {info['branch']}"
    if not scope:
        raise BhashaError("say which glossary: --lang <code> or --common")
    if scope != "common":
        Catalog.at(target).language(scope)
    rule, change = add_rule(target, scope, term, rule_kind, decided_by, room, translation=translation, note=note,
                            release=release.norm(rel) if rel else None)
    where = "every language" if scope == "common" else Catalog.at(target).language(scope)["name"]
    sha = gitops.commit(target, [Path("glossary") / f"{scope}.json"],
                        f"Glossary ({where}): {change}, decided by {decided_by} in {room}")
    return {"rule": rule, "change": change, "where": where, "commit": sha, "worktree": target, "push": push}


def cmd_glossary_add(args) -> int:
    res = record_decision(Path(args.C or Path.cwd()), "common" if args.common else args.lang, args.term, args.rule,
                          args.decided_by, args.room, rel=args.release, translation=args.translation, note=args.note or "")
    print(f"Recorded for {res['where']}: {res['change']} ({res['rule'].provenance()})" + (f" in commit {res['commit']}" if res["commit"] else ""))
    if res["push"]:
        print(f"Share it with the other languages: {res['push']}")
    return 0


def cmd_glossary_show(args) -> int:
    root = find_root(args.C)
    scopes = ["common", args.lang] if args.lang else ["common"] + Catalog.at(root).target_languages()
    data, lines = {}, []
    for scope in scopes:
        g = load_glossary(Catalog.at(root).read, scope)
        data[scope] = g
        lines.append(f"{scope}: " + ("; ".join(
            f"{t['rule']} \"{t['term']}\"" + (f" → \"{t['translation']}\"" if t.get("translation") else "") + f" ({t['decided_by']})"
            for t in g.get("terms", [])) or "no rules"))
    out(args, data, "\n".join(lines))
    return 0


# ── release ──────────────────────────────────────────────────────────────


def _status_line(r: dict) -> str:
    if r["state"] == "not started":
        return f"{WAIT} {r['language']}: not started"
    prefix = f"{r['name']} ({r['language']}) · {r['translated']}/{r['total']} strings"
    a = r.get("approval") or {}
    if r["state"] == "approved":
        where = f" in {a['approved_in']}" if a.get("approved_in") else ""
        return f"{OK} {prefix} · approved by {a['approved_by']}{where} at {a['approved_at']} · checks pass"
    if r["state"] == "approval stale":
        return f"{WARN} {prefix} · {a.get('approved_by', 'the reviewer')}'s approval no longer counts: {r['approval_state']}"
    if r["state"] == "in review":
        return f"{WAIT} {prefix} · checks pass · waiting for the reviewer"
    return f"{FAIL} {prefix} · {len(r['errors'])} checks fail"


def _verify_root(cwd: Path, rel: str) -> Path:
    """The checkout to verify: the release worktree when there is one, else this checkout."""
    root = find_root(cwd)
    if gitops.is_repo(root) and gitops.current_branch(root) != release.branch(rel):
        wt = gitops.worktree_for(root, release.branch(rel))
        if wt:
            return wt
    return root


def cmd_release(args) -> int:
    cwd = Path(args.C or Path.cwd()).resolve()
    if args.action == "start":
        info = release.start(cwd, args.release, by=args.by)
        text = [f"Release {info['release']}: branch {info['branch']} in {info['worktree']}"
                + (f" (manifest in commit {info['opened_commit']})" if info["opened_commit"] else "")]
        text.append(f"Push it so translators can start: {info['push']}" if info["push"] else "Already on origin.")
        out(args, info, "\n".join(text))
    elif args.action == "lang":
        info = release.lang(cwd, args.release, args.lang)
        out(args, info, f"{info['branch']} is ready in {info['worktree']}\n"
                        f"Next: cd {info['worktree']} && uv run bhasha brief {args.lang}")
    elif args.action == "sync":
        info = release.sync(cwd, args.release, args.lang)
        cat = Catalog.at(Path(info["worktree"]))
        found = findings_for(cat, args.lang)
        print(f"Merged {info['merged']} into {release.branch(args.release, args.lang)} ({info['before']} → {info['after']})")
        print(render_findings(cat, args.lang, found))
        return 1 if errors(found) else 0
    elif args.action == "approve":
        info = release.approve(cwd, args.release, args.lang, args.by, room=args.room)
        where = f" in {info['approved_in']}" if info["approved_in"] else ""
        out(args, info, f"{OK} Recorded: {info['language_name']} approved by {info['approved_by']}{where} at {info['approved_at']}"
                        + (f" (commit {info['commit']})" if info["commit"] else "")
                        + f"\nBound to these exact strings: {info['strings_sha256']}"
                        + f"\nPublish it: {info['push']}")
    elif args.action == "status":
        info = release.status(cwd, args.release)
        lines = [f"Release {info['release']} · branch {info['branch']}"] + [_status_line(r) for r in info["languages"]]
        out(args, info, "\n".join(lines))
    elif args.action == "verify":
        result = release.verify_tree(_verify_root(cwd, args.release), args.release)
        out(args, result, report.verify_text(result))
        write_summary(args.summary, report.verify_markdown(result))
        return 0 if result["ok"] else 1
    elif args.action == "merge":
        info = release.merge(cwd, args.release)
        verified = release.verify_tree(Path(info["worktree"]), args.release)
        text = [f"{OK} Merged {', '.join(info['languages'])} into {release.branch(args.release)} (notes: {info['notes']})",
                report.verify_text(verified),
                f"Push: {info['push']}"]
        text.append(f"Pull request: {info['pr']}" if info["pr"] else "Origin isn't GitHub, so post the notes in the room instead of opening a pull request.")
        out(args, info, "\n".join(text))
    return 0


# ── doctor / ci ──────────────────────────────────────────────────────────


def cmd_doctor(args) -> int:
    root = find_root(args.C)
    results = doctor.run(root, offline=args.offline)
    out(args, results, report.doctor_text(results))
    write_summary(args.summary, "## Bhasha Desk doctor\n\n" + report.doctor_markdown(results))
    return 1 if doctor.failed(results) else 0


def cmd_ci(args) -> int:
    root = find_root(args.C)
    head_ref = args.head_ref if args.head_ref is not None else os.environ.get("GITHUB_HEAD_REF", "")
    ok, text, markdown = ci.run(root, head_ref)
    print(text)
    write_summary(args.summary if args.summary is not None else os.environ.get("GITHUB_STEP_SUMMARY"), markdown)
    return 0 if ok else 1


# ── serve / demo ─────────────────────────────────────────────────────────


def cmd_serve(args) -> int:
    root = find_root(args.C)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root / "demo-app"))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(f"PaySathi demo app: http://127.0.0.1:{args.port}/  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def cmd_demo_run(args) -> int:
    from bhasha import demo
    return demo.run(Path(args.C) if args.C else None, keep=Path(args.keep) if args.keep else None,
                    serve=args.serve, port=args.port)


def cmd_demo_clean(args) -> int:
    cwd = Path(args.C or Path.cwd()).resolve()
    prefix = release.branch(args.release)
    known = set(Catalog.at(find_root(cwd)).languages)

    def ours(name: str) -> bool:
        return name == prefix or (name.startswith(prefix + "-") and name[len(prefix) + 1:] in known)

    branches = [b for b in gitops.git(cwd, "for-each-ref", "--format=%(refname:short)", "refs/heads/l10n/").splitlines() if ours(b)]
    for b in branches:
        wt = gitops.worktree_for(cwd, b)
        if wt:
            gitops.git(cwd, "worktree", "remove", "--force", str(wt))
            print(f"removed worktree {wt}")
        gitops.git(cwd, "branch", "-D", b)
        print(f"deleted branch {b}")
    if args.remote and gitops.has_remote(cwd):
        gitops.fetch(cwd)
        remote = [b[len("origin/"):] for b in gitops.git(cwd, "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin/l10n/").splitlines()]
        remote = [b for b in remote if ours(b)]
        if remote:
            gitops.git(cwd, "push", "origin", "--delete", *remote)
            print("deleted on origin: " + ", ".join(remote))
    print(f"Release {release.norm(args.release)} is clean.")
    return 0


# ── parser ───────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bhasha", description="Bhasha Desk: localise every release, checked and approved.")
    p.add_argument("--version", action="version", version=f"bhasha {__version__}")
    p.add_argument("-C", metavar="PATH", help="run as if started in PATH")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("status", help="how far each language is")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("brief", help="what a translator needs for one language")
    s.add_argument("lang")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_brief)

    s = sub.add_parser("check", help="run every deterministic check")
    s.add_argument("langs", nargs="*")
    s.add_argument("--allow-missing", action="store_true", help="strings not translated yet aren't failures")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("apply", help="write translations from a JSON file (or - for stdin)")
    s.add_argument("lang")
    s.add_argument("file")
    s.add_argument("--no-commit", action="store_true")
    s.set_defaults(func=cmd_apply)

    g = sub.add_parser("glossary", help="glossary decisions").add_subparsers(dest="action", required=True)
    s = g.add_parser("add", help="record a reviewer's decision")
    scope = s.add_mutually_exclusive_group()
    scope.add_argument("--lang")
    scope.add_argument("--common", action="store_true", help="applies to every language (brand terms)")
    s.add_argument("--term", required=True)
    s.add_argument("--rule", required=True, choices=["keep", "prefer", "avoid"])
    s.add_argument("--translation")
    s.add_argument("--decided-by", required=True)
    s.add_argument("--room", required=True)
    s.add_argument("--note")
    s.add_argument("--release", help="record it on the release branch, shared with every language")
    s.set_defaults(func=cmd_glossary_add)
    s = g.add_parser("show")
    s.add_argument("--lang")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_glossary_show)

    r = sub.add_parser("release", help="release branches, approvals, verification and merging").add_subparsers(dest="action", required=True)
    for name, helptext, extra in (
        ("start", "open a release branch with its manifest", []),
        ("lang", "give a language its own worktree", ["lang"]),
        ("sync", "bring glossary decisions into a language", ["lang"]),
        ("approve", "record a reviewer's approval of exactly these strings", ["lang"]),
        ("status", "every language's state in a release", []),
        ("verify", "the release gate: every language translated, checked and approved (CI runs this)", []),
        ("merge", "merge approved languages, verify, and write the notes", []),
    ):
        s = r.add_parser(name, help=helptext)
        s.add_argument("release")
        for e in extra:
            s.add_argument(e)
        if name == "start":
            s.add_argument("--by", help="who asked for the release")
        if name == "approve":
            s.add_argument("--by", required=True, help="the reviewer's name")
            s.add_argument("--room", help="the room the reviewer approved in")
        if name == "verify":
            s.add_argument("--summary", metavar="FILE", help="also append a Markdown report to FILE")
        s.add_argument("--json", action="store_true")
        s.set_defaults(func=cmd_release)

    s = sub.add_parser("doctor", help="preflight: data files, remote, GitHub login")
    s.add_argument("--offline", action="store_true", help="only check the data files")
    s.add_argument("--summary", metavar="FILE", help="also append a Markdown report to FILE")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("ci", help="what CI checks for a branch (the release gate on l10n/r<release>)")
    s.add_argument("--head-ref", help="the pull request's branch (default: $GITHUB_HEAD_REF)")
    s.add_argument("--summary", metavar="FILE", help="Markdown report file (default: $GITHUB_STEP_SUMMARY)")
    s.set_defaults(func=cmd_ci)

    s = sub.add_parser("serve", help="serve the PaySathi demo app")
    s.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    s.set_defaults(func=cmd_serve)

    d = sub.add_parser("demo", help="demo helpers").add_subparsers(dest="action", required=True)
    s = d.add_parser("run", help="replay a whole release in seconds, with no Switch and no AI")
    s.add_argument("--serve", action="store_true", help="then serve the shipped app")
    s.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    s.add_argument("--keep", metavar="DIR", help="build the replay in DIR (empty or new) and keep it")
    s.set_defaults(func=cmd_demo_run)
    s = d.add_parser("clean", help="delete a release's branches and worktrees to record again")
    s.add_argument("release")
    s.add_argument("--remote", action="store_true", help="also delete the branches on origin")
    s.set_defaults(func=cmd_demo_clean)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except BhashaError as exc:
        print(f"bhasha: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
