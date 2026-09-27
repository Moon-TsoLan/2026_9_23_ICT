"""Compare step-2A package scope against the small gold in eval/gold/steps."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "eval" / "gold" / "steps"
RUNS = ROOT / "work" / "runs"


def main() -> None:
    mismatches = []
    for path in sorted(GOLD.glob("*.json")):
        expected = json.loads(path.read_text(encoding="utf-8"))
        actual_path = RUNS / expected["announcement_id"] / "02_html_tables.json"
        if not actual_path.exists():
            mismatches.append(f"{expected['announcement_id']} missing run")
            continue
        actual = {item["table_index"]: item for item in json.loads(actual_path.read_text(encoding="utf-8"))["tables"]}
        for table in expected["tables"]:
            got = actual.get(table["table_index"])
            if got is None or got.get("package_scope") != table["package_scope"] or got.get("table_role") != table.get("table_role", got.get("table_role")):
                mismatches.append(f"{expected['announcement_id']} table {table['table_index']} expected {table} got {got}")
    if mismatches:
        raise SystemExit("\n".join(mismatches))
    print(f"step gold ok ({len(list(GOLD.glob('*.json')))} files)")


if __name__ == "__main__":
    main()
