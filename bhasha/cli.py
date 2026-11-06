"""bhasha: the command-line tool the agents (and people) run.

Output is written to be pasted into a chat: one line per item, no tables,
because Slack and Telegram don't render Markdown tables.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
import sys
from pathlib import Path

from bhasha import BhashaError, __version__, gitops, release
from bhasha.catalog import Catalog, LOCALES, dump, find_root, ordered, write_json
from bhasha.checker import check, errors
from bhasha.glossary import add_rule, load as load_glossary, rules_for

OK, FAIL, WAIT = "✅", "❌", "⏳"


def out(args, data, text: str) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2) if getattr(args, "json", False) else text)


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
        failed = failed or bool(errors(found))
        data[code] = [f.as_dict() for f in found]
        blocks.append(render_findings(cat, code, found))
    out(args, data, "\n\n".join(blocks))
    return 1 if failed else 0


# ── apply ────────────────────────────────────────────────────────────────


def cmd_apply(args) -> int:
    root = find_root(args.C)
    cat = Catalog.at(root)
    info = cat.language(args.lang)
    raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    try:
        incoming = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BhashaError(f"{args.file} is not valid JSON: {exc}") from exc
    if not isinstance(incoming, dict) or not all(isinstance(v, str) for v in incoming.values()):
        raise BhashaError('expected a JSON object of {"key": "translation"}')
    eng = cat.english
    unknown = [k for k in incoming if k not in eng]
    if unknown:
        raise BhashaError("these keys aren't in en.json: " + ", ".join(unknown))

    created = not cat.has_locale(args.lang)
    current = cat.locale(args.lang)
    changed = [k for k, v in incoming.items() if current.get(k) != v]
    current.update(incoming)
    path = root / LOCALES / f"{args.lang}.json"
    write_json(path, ordered(eng, current))
    paths = [path.relative_to(root)]
    if created:
        index = cat.index
        if args.lang not in index:
            index.append(args.lang)
            write_json(root / LOCALES / "index.json", index)
            paths.append(Path(LOCALES) / "index.json")

    sha = None
    if not args.no_commit and changed:
        verb = "Translate" if created or len(changed) > 1 else "Update"
        sha = gitops.commit(root, paths, f"{verb} {len(changed)} {info['name']} string{'s' if len(changed) != 1 else ''} ({args.lang})")
    found = findings_for(Catalog.at(root), args.lang)
    summary = f"Applied {len(changed)} change{'s' if len(changed) != 1 else ''} to {lang_label(cat, args.lang)}" + (f" in commit {sha}" if sha else "")
    print(summary)
    print(render_findings(Catalog.at(root), args.lang, found))
    return 1 if errors(found) else 0


# ── glossary ─────────────────────────────────────────────────────────────


def cmd_glossary_add(args) -> int:
    start_dir = Path(args.C or Path.cwd())
    target = find_root(start_dir)
    push = None
    if args.release:
        info = release.start(start_dir, args.release)
        target = Path(info["worktree"])
        push = f"git -C {target} push origin {info['branch']}"
    scope = "common" if args.common else args.lang
    if not scope:
        raise BhashaError("say which glossary: --lang <code> or --common")
    if scope != "common":
        Catalog.at(target).language(scope)
    rule, change = add_rule(target, scope, args.term, args.rule, args.decided_by, args.room,
                            translation=args.translation, note=args.note or "",
                            release=release.norm(args.release) if args.release else None)
    where = "every language" if scope == "common" else Catalog.at(target).language(scope)["name"]
    sha = gitops.commit(target, [Path("glossary") / f"{scope}.json"],
                        f"Glossary ({where}): {change}, decided by {args.decided_by} in {args.room}")
    print(f"Recorded for {where}: {change} ({rule.provenance()})" + (f" in commit {sha}" if sha else ""))
    if push:
        print(f"Share it with the other languages: {push}")
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


def cmd_release(args) -> int:
    cwd = Path(args.C or Path.cwd()).resolve()
    if args.action == "start":
        info = release.start(cwd, args.release)
        text = [f"Release {info['release']}: branch {info['branch']} in {info['worktree']}"]
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
        info = release.approve(cwd, args.release, args.lang, args.by)
        out(args, info, f"{OK} Recorded: {info['language_name']} approved by {info['approved_by']} at {info['approved_at']}"
                        + (f" (commit {info['commit']})" if info["commit"] else "") + f"\nPublish it: {info['push']}")
    elif args.action == "status":
        info = release.status(cwd, args.release)
        lines = [f"Release {info['release']} · branch {info['branch']}"]
        for r in info["languages"]:
            if r["state"] == "not started":
                lines.append(f"{WAIT} {r['language']}: not started")
                continue
            prefix = f"{r['name']} ({r['language']}) · {r['translated']}/{r['total']} strings"
            if r["state"] == "approved":
                a = r["approval"]
                lines.append(f"{OK} {prefix} · approved by {a['approved_by']} at {a['approved_at']} · checks pass")
            elif r["state"] == "in review":
                lines.append(f"{WAIT} {prefix} · checks pass · waiting for the reviewer")
            else:
                lines.append(f"{FAIL} {prefix} · {len(r['errors'])} checks fail")
        out(args, info, "\n".join(lines))
    elif args.action == "merge":
        info = release.merge(cwd, args.release)
        text = [f"{OK} Merged {', '.join(info['languages'])} into {release.branch(args.release)} (notes: {info['notes']})",
                f"Push: {info['push']}"]
        text.append(f"Pull request: {info['pr']}" if info["pr"] else "Origin isn't GitHub, so post the notes in the room instead of opening a pull request.")
        out(args, info, "\n".join(text))
    return 0


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

    r = sub.add_parser("release", help="release branches, approvals and merging").add_subparsers(dest="action", required=True)
    for name, helptext, extra in (
        ("start", "open a release branch", []),
        ("lang", "give a language its own worktree", ["lang"]),
        ("sync", "bring glossary decisions into a language", ["lang"]),
        ("approve", "record a reviewer's approval", ["lang"]),
        ("status", "every language's state in a release", []),
        ("merge", "merge approved languages and write the notes", []),
    ):
        s = r.add_parser(name, help=helptext)
        s.add_argument("release")
        for e in extra:
            s.add_argument(e)
        if name == "approve":
            s.add_argument("--by", required=True, help="the reviewer's name")
        s.add_argument("--json", action="store_true")
        s.set_defaults(func=cmd_release)

    s = sub.add_parser("serve", help="serve the PaySathi demo app")
    s.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    s.set_defaults(func=cmd_serve)

    d = sub.add_parser("demo", help="demo helpers").add_subparsers(dest="action", required=True)
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
