"""Pick a fixed holdout of notices that are not in benchmark10.

Run benchmark10 two or three times after a prompt or rule change, then run this
holdout once. Do not tune rules on the holdout ids.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "data" / "赛题五基准测试数据" / "赛题五.基准测试数据_html"
BENCHMARK = ROOT / "eval" / "benchmark10.json"
OUT = ROOT / "eval" / "holdout25.json"


def main() -> None:
    used = {item["announcement_id"] for item in json.loads(BENCHMARK.read_text(encoding="utf-8"))["announcements"]}
    names = sorted(path.stem for path in HTML.glob("*.html") if path.stem not in used)
    step = max(1, len(names) // 25)
    chosen = names[::step][:25]
    OUT.write_text(
        json.dumps({"name": "holdout25", "announcements": [{"announcement_id": item} for item in chosen]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {len(chosen)} ids to {OUT}")


if __name__ == "__main__":
    main()
