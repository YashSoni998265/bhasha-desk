# Bhasha Desk architecture

Bhasha Desk splits the work in two:
- **Agents in Switch rooms** do the language and the conversation: drafting, taking corrections, carrying decisions between rooms.
- **The deterministic `bhasha` command** does everything that must be exactly right: checks, glossary records, approvals, git, the release gate.

The agents call the command; they never edit records by hand.

```
 Switch (rooms, agents, approvals)              git remote (GitHub)              CI (GitHub Actions)
 ─────────────────────────────────              ───────────────────              ───────────────────
 #hub   bhasha-scout       ── bhasha ──▶  l10n/r2.4         (manifest, glossary)
 #hi    bhasha-translator  ── bhasha ──▶  l10n/r2.4-hi      (hi.json, approval)
 #ta    bhasha-translator  ── bhasha ──▶  l10n/r2.4-ta      (ta.json, approval)
 #ship  bhasha-release     ── bhasha ──▶  l10n/r2.4 merged ──── pull request ────▶  bhasha ci → verify
```

## Branches

| Branch | Written by | Holds |
|---|---|---|
| `main` | people | the app as it ships |
| `l10n/r<R>` | the scout, then the release agent | `releases/r<R>/RELEASE.json`, every glossary decision of the release, and finally the merge of every language plus `RELEASE_NOTES.md` |
| `l10n/r<R>-<lang>` | the translator, in that language's room | the language's locale file, `index.json` for a new language, `releases/r<R>/approvals/<lang>.json` |

- **One glossary writer.** Only the scout writes the glossary, and only on `l10n/r<R>`, so there is one canonical copy and language branches never conflict. A language picks a decision up with `bhasha release sync`, which merges `l10n/r<R>` into it. Until then, `release status` already judges the language against the release branch's glossary, so a decision counts the moment it is published.
- **A rule is recorded before any string changes, even in the room it came from.** Until "keep AutoPay" is in the glossary, "AutoPay" in Latin letters inside a Tamil string is, correctly, English left behind. So the translator hands the rule to the scout first. The scout records it and asks every language room to sync, and each room's check then points at exactly the strings to change.
- **One worktree per language.** The translator agent serves every room, with one session per room. Each language gets its own git worktree (`wt-r<R>-<lang>`) so parallel sessions never share a working tree; lock contention on the shared object store is retried.

## Records

All records are JSON in git, written only by `bhasha`.

**`releases/r<R>/RELEASE.json`**, written by `bhasha release start --by`:
```json
{"release": "2.4", "opened_by": "Yash", "opened_at": "2026-11-07T11:32:05+00:00",
 "base_commit": "3dfdc8f", "new_strings": ["autopay.title", "..."], "languages": ["hi", "ta"], "tool": "bhasha 1.0.0"}
```

**`releases/r<R>/approvals/<lang>.json`**, written by `bhasha release approve --by --room`:
```json
{"release": "2.4", "language": "ta", "language_name": "Tamil",
 "approved_by": "Arjun", "approved_in": "bhasha-ta", "approved_at": "2026-11-07 11:44 UTC",
 "approved_commit": "d24a099", "strings": 18, "checker": "pass",
 "strings_sha256": "sha256:70cbe264…", "source_sha256": "sha256:…", "glossary_sha256": "sha256:…",
 "tool": "bhasha 1.0.0"}
```

**`glossary/<scope>.json`**, written by `bhasha glossary add`. Scope is `common` (brand terms, every language) or a language code. `terms` is the current truth, one rule per term and kind. `log` is append-only: every change, with who, which room, when and which release.

## Approval integrity

An approval is a statement about specific text, so it records digests of that text:

- `strings_sha256` = SHA-256 of the language's locale file
- `source_sha256` = SHA-256 of `{"english": en.json, "meta": meta.json}`, so changing a string's English, context or length limit voids approvals given for it
- `glossary_sha256` = SHA-256 of the rules in force, kept for the record

All three are taken over canonical JSON (keys sorted, no whitespace, UTF-8), so reformatting a file changes nothing and changing one character changes the digest.

Approvals are checked in three places:

| Where | What happens to a stale approval |
|---|---|
| `bhasha apply` | Warns the translator that the edit voided the approval, so it asks the reviewer again |
| `bhasha release status` | Shows the language as `approval stale` with the reason |
| `bhasha release merge` / `verify` / CI | Refuses to ship: `strings changed after the approval` or `the English changed after the approval` |

`merge` also publishes `demo-app/locales/approvals.json` (release, and per language: approver, room, time, `strings_sha256`). The PaySathi app reads it, recomputes the digest of the strings it is showing with WebCrypto, using the same canonical JSON (a test runs the app's own code under Node and compares it with Python's), and shows either "✓ approved by … verified in your browser" or "⚠ changed after … approved".

Approving also requires that the language branch already contains everything published on `l10n/r<R>` (`merge-base --is-ancestor`), so nobody approves work that hasn't seen the latest glossary decisions.

## A language's state in a release

```
not started ──lang──▶ failing checks ◀──apply──▶ in review ──approve──▶ approved
                           ▲                                               │
                           └──── a new glossary rule it breaks ────────────┤
                                                                           ▼
                                  approve again ◀──── approval stale (strings or English changed)
```

`merge` ships only when every language the app ships is `approved`. A language added mid-release (`@scout add Bengali`) joins the set as soon as its branch exists.

## The release gate

`bhasha release verify <R>` runs on one checkout. It passes only when every language in `index.json` (other than English) meets all of these:
1. every English string is translated
2. every check passes against the glossary in that checkout
3. it has an approval for release `<R>` whose digests match, naming a reviewer

`merge` runs it on the merged branch before anything is pushed. CI runs it on the pull request's merge commit with `main`. So if `main` changed the English after the reviewers approved, the gate fails, which is correct: they approved something else.

## CI

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) has two jobs: the test suite (Python 3.9 and 3.13) and the l10n gate. The gate is a single command, `bhasha ci`, which reads the pull request's branch:

| Branch | Check |
|---|---|
| `l10n/r<R>` | the release gate for `<R>` |
| `l10n/r<R>-<lang>` | that language passes every check |
| anything else | data health (`doctor --offline`), and every translation that exists passes; strings still to translate aren't failures |

Each run writes a Markdown table to the job summary. Actions are pinned by commit SHA.

## The checker

| Rule | Fails when |
|---|---|
| missing, empty | a string has no translation |
| untranslated | the translation equals the English and the English has words that aren't kept terms |
| placeholder | a `{name}` is lost or invented |
| tags | `<b>` and friends don't match the English |
| length | longer than `max_len`, counting what a reader sees: combining vowel signs and viramas don't add width |
| english-left | Latin words remain that aren't kept terms |
| script | letters from another script, e.g. Devanagari in Tamil |
| glossary | a keep / prefer / avoid rule is broken; the message names who decided it and where |
| consistency (warning) | the same English is translated two different ways |

## Switch usage, per agent

| Agent | Addressing | Uses |
|---|---|---|
| scout | owner and owner's agents | `list_linked_rooms` (the hub's `language:` links are the registry), `connect_to_room`, `send_targeted_message`, `read_context` for the banner thread, `get_room_detail`, `get_my_context`, `create_room`, `link_rooms` |
| translator | anyone in its rooms | room instructions for its bindings; `connect_to_room` and `send_targeted_message` to hand rules to the scout |
| release | owner only | `assume_role` for the exclusive `release-captain` |

Every agent runs in auto mode with `auto_approve: false`: reading and checks run on their own, while pushing a branch and opening the pull request wait for a person's Allow in the room.

## Failure handling

| Situation | What happens |
|---|---|
| A check fails after a draft | The translator fixes and re-applies before showing anyone; `apply` prints the failures |
| A reviewer's rule breaks other languages | Their status flips to `failing checks` at once; `sync` shows exactly what to fix |
| An approval gets stale | `merge` names the language and the reason, and pushes nothing |
| Merging breaks a check | `merge` stops after verifying the merged tree and pushes nothing |
| The remote or `gh` login is broken | `bhasha doctor` reports it at kickoff |
| Two sessions touch git at once | Separate worktrees; lock errors are retried with backoff |

## The replay

`bhasha demo run` ([`bhasha/demo.py`](../bhasha/demo.py)) builds a bare git repository as a stand-in for GitHub, plus a checkout for each agent. It then plays release 2.4 through the same functions the agents' commands call, with canned translations ([`bhasha/demo_data.py`](../bhasha/demo_data.py)) in place of the model:
- the drafts, then the rule recorded once and enforced in both languages
- approvals bound to the strings
- an edit after approval, refused at ship
- CI passing on a clean clone, then failing on a tampered string

It takes about 7 seconds. `--serve` then shows the shipped app with its approvals verified in the browser. [`tests/test_demo.py`](../tests/test_demo.py) runs it on every CI build.

