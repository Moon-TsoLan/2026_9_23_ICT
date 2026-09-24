"""2022 procurement catalog lookup. Match only the level that is actually hit."""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path

from ict.config import CATALOG_PATH

CODE_RE = re.compile(r"\b([ABC]\d{8})\b")


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text).replace("／", "/").strip()


class Catalog:
    def __init__(self, path: Path = CATALOG_PATH) -> None:
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.entries: dict[str, dict] = payload["entries"]
        self.by_path: dict[str, dict] = {}
        self.by_name: dict[str, list[dict]] = {}
        for entry in self.entries.values():
            self.by_path[_norm(entry["path"])] = entry
            self.by_name.setdefault(_norm(entry["name"]), []).append(entry)

    def match(self, raw_code: str | None, raw_name: str | None) -> dict:
        code = (raw_code or "").strip().upper()
        name = (raw_name or "").strip()
        found_code = CODE_RE.search(code) or CODE_RE.search(name)
        if found_code and found_code.group(1) in self.entries:
            entry = self.entries[found_code.group(1)]
            conflict = bool(name) and _norm(name) not in {_norm(entry["name"]), _norm(entry["path"])}
            return self._hit("exact_code", entry, conflict)
        if "/" in name or "／" in name:
            entry = self.by_path.get(_norm(name))
            if entry:
                return self._hit("exact_path", entry, False)
        if name:
            hits = self.by_name.get(_norm(name), [])
            if len(hits) == 1:
                kind = "exact_name" if _norm(name) == _norm(hits[0]["name"]) else "normalized_name"
                if hits[0]["name"] == name:
                    kind = "exact_name"
                if not hits[0]["leaf"] and kind == "exact_name":
                    kind = "parent_name"
                return self._hit(kind, hits[0], False)
            if len(hits) > 1:
                return {"match_type": "unmatched", "ambiguous_name": True}
            fuzzy = self._fuzzy(name)
            if fuzzy:
                return self._hit("fuzzy", fuzzy, False)
        return {"match_type": "unmatched"}

    def _hit(self, match_type: str, entry: dict, conflict: bool) -> dict:
        return {
            "match_type": match_type,
            "matched_code": entry["code"],
            "matched_name": entry["name"],
            "matched_level": entry["level"],
            "leaf": entry["leaf"],
            "category_type": entry["code"][0],
            "conflict": conflict,
        }

    def _fuzzy(self, name: str) -> dict | None:
        key = _norm(name)
        if len(key) < 4:
            return None
        best: tuple[float, dict] | None = None
        second = 0.0
        for norm_name, entries in self.by_name.items():
            if len(entries) != 1:
                continue
            score = SequenceMatcher(None, key, norm_name).ratio()
            if best is None or score > best[0]:
                second = best[0] if best else 0.0
                best = (score, entries[0])
            elif score > second:
                second = score
        if best and best[0] >= 0.92 and best[0] - second >= 0.03:
            return best[1]
        return None
