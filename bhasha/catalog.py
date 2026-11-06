"""The string catalog: English source, context, languages and translations.

Every read goes through a reader function, so the same code can look at the
working tree or at any git ref (see gitops.ref_reader).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

from bhasha import BhashaError

LOCALES = "demo-app/locales"
Reader = Callable[[str], Optional[str]]


def find_root(start: Optional[Path] = None) -> Path:
    here = Path(start or Path.cwd()).resolve()
    for cand in (here, *here.parents):
        if (cand / "languages.json").is_file() and (cand / LOCALES).is_dir():
            return cand
    raise BhashaError(f"run this inside a bhasha-desk checkout: no languages.json above {here}")


def file_reader(root: Path) -> Reader:
    def read(rel: str) -> Optional[str]:
        path = root / rel
        return path.read_text(encoding="utf-8") if path.is_file() else None

    return read


def dump(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump(data), encoding="utf-8")


class Catalog:
    def __init__(self, read: Reader):
        self.read = read

    @classmethod
    def at(cls, root: Path) -> "Catalog":
        return cls(file_reader(root))

    def _json(self, rel: str, default=None):
        text = self.read(rel)
        if text is None:
            if default is not None:
                return default
            raise BhashaError(f"{rel} is missing")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise BhashaError(f"{rel} is not valid JSON: {exc}") from exc

    @property
    def english(self) -> dict:
        return self._json(f"{LOCALES}/en.json")

    @property
    def meta(self) -> dict:
        return self._json(f"{LOCALES}/meta.json", {})

    @property
    def languages(self) -> dict:
        return self._json("languages.json")

    @property
    def index(self) -> list:
        return self._json(f"{LOCALES}/index.json", ["en"])

    def locale(self, code: str) -> dict:
        return self._json(f"{LOCALES}/{code}.json", {})

    def has_locale(self, code: str) -> bool:
        return self.read(f"{LOCALES}/{code}.json") is not None

    def language(self, code: str) -> dict:
        langs = self.languages
        if code not in langs:
            known = ", ".join(f"{c} ({v['name']})" for c, v in langs.items())
            raise BhashaError(f"unknown language code {code!r}. Known: {known}")
        return langs[code]

    def target_languages(self) -> list:
        """Languages the app ships, in index order, without English."""
        return [c for c in self.index if c != "en"]


def ordered(english: dict, data: dict) -> dict:
    """Return translations in the English file's key order, extras last."""
    out = {k: data[k] for k in english if k in data}
    out.update({k: data[k] for k in sorted(data) if k not in english})
    return out
