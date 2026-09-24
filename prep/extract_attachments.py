"""Recursively extract contest attachment archives into per-announcement directories.

Preparatory step only. The main extraction pipeline does not call this module.
Original archives under data/ are left in place.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

SEVEN_ZIP = Path(r"D:\学习工作\7-Zip\7z.exe")
ARCHIVE_SUFFIXES = {".zip", ".rar", ".7z"}
MAX_DEPTH = 8
MARKER = ".extract_complete"
INVALID_CHARS = '<>:"|?*'


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_source() -> Path:
    return repo_root() / "data" / "赛题五基准测试数据" / "赛题五.基准测试数据_file"


def default_output() -> Path:
    return repo_root() / "work" / "attachments"


def decode_zip_name(info: zipfile.ZipInfo) -> str:
    name = info.filename
    if info.flag_bits & 0x800:
        return name
    raw = name.encode("cp437", errors="replace")
    for encoding in ("gbk", "utf-8"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return name


def sanitize_part(part: str) -> str:
    cleaned = "".join("_" if ch in INVALID_CHARS or ord(ch) < 32 else ch for ch in part)
    cleaned = cleaned.rstrip(" .")
    reserved = {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
    if cleaned.upper().split(".")[0] in reserved:
        cleaned = f"_{cleaned}"
    return cleaned or "_"


def safe_target(root: Path, member_name: str) -> Path | None:
    relative = member_name.replace("\\", "/").lstrip("/")
    if not relative:
        return None
    parts: list[str] = []
    for part in relative.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            return None
        parts.append(sanitize_part(part))
    if not parts:
        return None
    target = root.joinpath(*parts)
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    return target


def unique_dir(path: Path) -> Path:
    if not path.exists():
        return path
    index = 2
    while True:
        candidate = path.with_name(f"{path.name}__{index}")
        if not candidate.exists():
            return candidate
        index += 1


def extract_zip(archive: Path, dest: Path, failures: list[dict]) -> bool:
    try:
        zf = zipfile.ZipFile(archive)
    except zipfile.BadZipFile as exc:
        failures.append({"archive": str(archive), "member": None, "reason": f"bad_zip:{exc}"})
        return False
    with zf:
        for info in zf.infolist():
            name = decode_zip_name(info)
            if name.endswith("/"):
                target = safe_target(dest, name)
                if target is None:
                    failures.append({"archive": str(archive), "member": name, "reason": "path_escape"})
                    continue
                target.mkdir(parents=True, exist_ok=True)
                continue
            target = safe_target(dest, name)
            if target is None:
                failures.append({"archive": str(archive), "member": name, "reason": "path_escape"})
                continue
            if target.exists():
                target = target.with_name(f"{target.stem}__dup{target.suffix}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
    return True


def extract_with_7z(archive: Path, dest: Path, failures: list[dict]) -> bool:
    if not SEVEN_ZIP.exists():
        failures.append({"archive": str(archive), "member": None, "reason": "7z_missing"})
        return False
    dest.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [str(SEVEN_ZIP), "x", "-y", "-bd", "-bso0", "-bsp0", str(archive), f"-o{dest}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode not in (0, 1):
        detail = (result.stderr or result.stdout or "").strip()[:500]
        failures.append({"archive": str(archive), "member": None, "reason": f"7z_exit_{result.returncode}:{detail}"})
        return False
    return True


def extract_archive(archive: Path, dest: Path, failures: list[dict]) -> bool:
    suffix = archive.suffix.lower()
    if suffix == ".zip":
        return extract_zip(archive, dest, failures)
    if suffix in {".rar", ".7z"}:
        return extract_with_7z(archive, dest, failures)
    failures.append({"archive": str(archive), "member": None, "reason": f"unsupported:{suffix}"})
    return False


def find_archives(root: Path) -> list[Path]:
    found: list[Path] = []
    if not root.exists():
        return found
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in ARCHIVE_SUFFIXES:
            found.append(path)
    return found


def extract_announcement(archive: Path, output_root: Path, force: bool) -> dict:
    announcement_id = archive.stem
    dest = output_root / announcement_id
    marker = dest / MARKER
    if marker.exists() and not force:
        saved = json.loads(marker.read_text(encoding="utf-8"))
        saved["skipped"] = True
        return saved
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    failures: list[dict] = []
    nested_extracted = 0
    max_depth = 0
    queue: list[tuple[Path, Path, int, bool]] = [(archive, dest, 0, True)]
    seen: set[str] = set()
    while queue:
        current, outdir, depth, is_outer = queue.pop(0)
        key = str(current.resolve())
        if key in seen:
            continue
        seen.add(key)
        max_depth = max(max_depth, depth)
        if depth > MAX_DEPTH:
            failures.append({"archive": str(current), "member": None, "reason": "max_depth"})
            continue
        ok = extract_archive(current, outdir, failures)
        if ok and not is_outer:
            current.unlink(missing_ok=True)
            nested_extracted += 1
        if not ok:
            continue
        for child in find_archives(outdir):
            child_key = str(child.resolve())
            if child_key in seen:
                continue
            queue.append((child, unique_dir(child.with_suffix("")), depth + 1, False))
    record = {
        "announcement_id": announcement_id,
        "status": "partial" if failures else "success",
        "max_depth": max_depth,
        "nested_extracted": nested_extracted,
        "failures": failures,
        "skipped": False,
    }
    marker.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def write_report(output_root: Path, source: Path, records: list[dict]) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    report = {
        "source_dir": str(source),
        "output_dir": str(output_root),
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "archive_count": len(records),
        "extracted": sum(1 for item in records if item["status"] == "success" and not item.get("skipped")),
        "partial": sum(1 for item in records if item["status"] == "partial"),
        "skipped": sum(1 for item in records if item.get("skipped")),
        "announcements": records,
    }
    path = output_root / "_extract_report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Recursively extract attachment archives by announcement id.")
    parser.add_argument("--source", type=Path, default=default_source())
    parser.add_argument("--output", type=Path, default=default_output())
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    archives = sorted(args.source.glob("*.zip"))
    if args.limit:
        archives = archives[: args.limit]
    args.output.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(extract_announcement, archive, args.output, args.force): archive
            for archive in archives
        }
        done = 0
        for future in as_completed(futures):
            done += 1
            record = future.result()
            records.append(record)
            if done % 20 == 0 or done == len(archives):
                print(f"{done}/{len(archives)} {record['announcement_id']} {record['status']}", flush=True)
                write_report(args.output, args.source, records)
    records.sort(key=lambda item: item["announcement_id"])
    path = write_report(args.output, args.source, records)
    print(f"report {path}", flush=True)


if __name__ == "__main__":
    main()
