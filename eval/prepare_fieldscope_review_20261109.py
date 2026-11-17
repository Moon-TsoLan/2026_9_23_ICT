"""Prepare a blind, full-source local review; never call APIs or change production routing."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OLD = ROOT / "work/attachment-coverage-expanded-20261109"
OUT = ROOT / "work/attachment-fieldscope-review-20261109"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare():
    import pymupdf
    from PIL import Image
    from ict.attachment_pages import source_pages, split_page
    from ict.parse.census import FileEntry

    manifest = read(OLD / "analysis38/manifest.json")
    old_rows = read(OLD / "analysis38/rows.json")
    old_keys = {r["page_key"] for r in old_rows}
    aids = sorted(manifest["contexts"])
    cases, errors, count = {}, [], Counter()
    for aid in aids:
        frozen = read(ROOT / "work/front-half-repair-20261108/live-capacity/contexts" / (aid + ".json"))
        context = {k: frozen[k] for k in ("project_name", "package_registry", "known_observations")}
        context["announcement_id"] = aid
        announcement = read(ROOT / "work/front-half-repair-20261108/live-capacity/runs" / aid / "01_announcement_understanding.json")
        context["known_project_identity"] = {k: announcement.get(k) for k in ("project_name", "purchaser", "source_project_no", "announcement_type")}
        context["known_summary_amount"] = announcement.get("summary_amount")
        context["known_package_amounts"] = [{k: package.get(k) for k in ("package_no", "package_name", "package_kind", "source_package_no", "package_amount")} for package in announcement["packages"]]
        write(OUT / "cases" / aid / "context.json", context)
        cases[aid] = {"announcement_id": aid, "project_name": frozen["project_name"], "files": [], "units": []}
    for index, source in enumerate(manifest["files"], 1):
        assert sha(source["path"]) == source["digest"], source["path"]
        key = hashlib.sha256((source["announcement_id"] + source["digest"]).encode()).hexdigest()[:16]
        prepared = read(OLD / "prepared" / (key + ".json"))
        existing = {p["unit_no"]: p for p in prepared.get("pages", [])}
        clean = {k: source[k] for k in ("announcement_id", "file_id", "file_name", "path", "digest", "format")}
        clean["file_key"] = key
        case = cases[source["announcement_id"]]
        clean["unit_keys"] = []
        case["files"].append(clean)

        def emit(number, page, *, image_path=None):
            old = existing.get(number)
            unit_key = key + "_%04d" % number
            segments = old["source_segments"] if old else page.get("source_segments", [{"text": page.get("text", "")}])
            visual = image_path or (old.get("reference_image") if old else None)
            if visual and not Path(visual).is_file():
                visual = None
            gate_images = old.get("gate_images", []) if old else page.get("image_paths", [])
            gate_images = [p for p in gate_images if Path(p).is_file()]
            unit = {"unit_key": unit_key, "announcement_id": source["announcement_id"],
                    "file_key": key, "file_id": source["file_id"], "file_name": source["file_name"],
                    "source_path": source["path"], "page_no": old["page_no"] if old else page.get("page_no", number),
                    "unit_no": number, "coordinate_kind": old["coordinate_kind"] if old else page.get("coordinate_kind", "pdf_page"),
                    "physical_page": old["physical_page"] if old else page.get("physical_page", True),
                    "source_segments": segments, "image": visual or (gate_images[0] if gate_images else None),
                    "has_image_content": bool(gate_images) if old else bool(page.get("has_image_content")),
                    "old_page_key": unit_key if unit_key in old_keys else None}
            write(OUT / "units" / (unit_key + ".json"), unit)
            case["units"].append(unit_key)
            clean["unit_keys"].append(unit_key)
            count["physical" if unit["physical_page"] else "logical"] += 1

        try:
            if source["format"] == "pdf":
                with pymupdf.open(source["path"]) as document:
                    clean["physical_pages"] = len(document)
                    for number, page in enumerate(document, 1):
                        image = None
                        if number not in existing:
                            text = page.get_text()
                            has_image = bool(page.get_images()) or not text.strip()
                            if has_image:
                                image = OUT / "images" / (key + "_%04d.jpg" % number)
                                image.parent.mkdir(parents=True, exist_ok=True)
                                page.get_pixmap(dpi=160).save(image)
                            data = {"page_no": number, "text": text, "has_image_content": has_image}
                        else:
                            data = {}
                        emit(number, data, image_path=str(image) if image else None)
            elif source["format"] == "image":
                with Image.open(source["path"]) as image:
                    total = getattr(image, "n_frames", 1)
                    for number in range(1, total + 1):
                        path = None
                        if number not in existing:
                            image.seek(number - 1)
                            path = OUT / "images" / (key + "_%04d.png" % number)
                            path.parent.mkdir(parents=True, exist_ok=True)
                            image.convert("RGB").save(path)
                        emit(number, {"page_no": number, "text": "", "coordinate_kind": "image_frame", "has_image_content": True},
                             image_path=str(path) if path else None)
            elif existing and all(p["physical_page"] for p in existing.values()) and (
                    max((b.get("pages_total", 0) for b in prepared.get("budget", [])), default=0) == len(existing)):
                for number in sorted(existing):
                    emit(number, {})
            else:
                entry = FileEntry(file_id=source["file_id"], name=source["file_name"], relative_path=source["file_name"],
                                  path=Path(source["path"]), size_bytes=Path(source["path"]).stat().st_size,
                                  declared_ext=Path(source["path"]).suffix, fmt=source["format"], note="", digest=source["digest"], pages=source.get("pages"))
                ordinal = 0
                with ExitStack() as stack:
                    for page in source_pages(entry, lambda proxy: list(range(1, proxy.pages + 1)), stack):
                        if page.get("coordinate_kind") in ("pdf_page", "rendered_office_page", "image_frame"):
                            ordinal += 1
                            path = None
                            if ordinal not in existing and page.get("parse_path"):
                                with pymupdf.open(page["parse_path"]) as doc:
                                    path = OUT / "images" / (key + "_%04d.jpg" % ordinal)
                                    path.parent.mkdir(parents=True, exist_ok=True)
                                    doc[page["page_no"] - 1].get_pixmap(dpi=160).save(path)
                            emit(ordinal, {**page, "physical_page": True, "has_image_content": bool(page.get("images"))}, image_path=str(path) if path else None)
                        else:
                            for chunk in split_page(entry.file_id, entry.name, page):
                                ordinal += 1
                                emit(ordinal, {**chunk, "physical_page": False})
        except Exception as exc:
            record = {"announcement_id": source["announcement_id"], "file_key": key,
                      "file_name": source["file_name"], "error": type(exc).__name__ + ":" + str(exc)}
            errors.append(record)
            clean["error"] = record["error"]
        if index % 20 == 0:
            print(json.dumps({"files_prepared": index, "units": sum(count.values()), "errors": len(errors)}), flush=True)
    for aid, case in cases.items():
        write(OUT / "cases" / aid / "index.json", case)
    groups = [[], [], []]
    loads = [0, 0, 0]
    for aid, case in sorted(cases.items(), key=lambda item: len(item[1]["units"]), reverse=True):
        slot = loads.index(min(loads))
        groups[slot].append(aid)
        loads[slot] += len(case["units"])
    write(OUT / "manifest.json", {"cases": [{"announcement_id": aid, "project_name": case["project_name"], "units": len(case["units"]), "files": len(case["files"])} for aid, case in cases.items()],
          "counts": dict(count), "errors": errors, "old_review_units": len(old_rows), "source_manifest_sha256": sha(OLD / "analysis38/manifest.json"),
          "business_contract_sha256": sha(ROOT / "doc/任务目标与最终字段口径.md"), "no_api": True, "no_ocr": True,
          "assignments": [{"group": i + 1, "units": loads[i], "cases": group} for i, group in enumerate(groups)]})
    print(json.dumps({"complete": True, "cases": len(cases), "units": sum(count.values()), "counts": dict(count), "errors": len(errors), "group_loads": loads}), flush=True)


def bundle(aid, start, count):
    case = read(OUT / "cases" / aid / "index.json")
    for key in case["units"][start:start + count]:
        unit = read(OUT / "units" / (key + ".json"))
        print(json.dumps({"unit_key": key, "file_name": unit["file_name"], "source_path": unit["source_path"],
                          "page_no": unit["page_no"], "coordinate_kind": unit["coordinate_kind"], "image": unit["image"],
                          "has_image_content": unit["has_image_content"], "segments": unit["source_segments"]}, ensure_ascii=False))


def sheet(keys, name):
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    tiles = []
    font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 20)
    for key in keys:
        unit = read(OUT / "units" / (key + ".json"))
        path = unit["image"]
        if not path:
            import pymupdf
            path = OUT / "images" / (key + ".jpg")
            path.parent.mkdir(parents=True, exist_ok=True)
            with pymupdf.open(unit["source_path"]) as doc:
                doc[unit["page_no"] - 1].get_pixmap(dpi=160).save(path)
        with Image.open(path) as image:
            tile = Image.new("RGB", (760, 1120), "#ddd")
            preview = ImageOps.contain(image.convert("RGB"), (750, 1060))
            tile.paste(preview, ((760-preview.width)//2, 52))
            ImageDraw.Draw(tile).text((8, 8), key + " p" + str(unit["page_no"]), font=font, fill="black")
            tiles.append(tile)
    output = Image.new("RGB", (760 * min(3, len(tiles)), 1120 * ((len(tiles)+2)//3)), "#aaa")
    for i, tile in enumerate(tiles):
        output.paste(tile, ((i%3)*760, (i//3)*1120))
    path = OUT / "sheets" / (name + ".jpg")
    path.parent.mkdir(parents=True, exist_ok=True)
    output.save(path, quality=90)
    print(str(path))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "bundle", "sheet"))
    parser.add_argument("--aid")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--keys", nargs="+")
    parser.add_argument("--name", default="review")
    args = parser.parse_args()
    if args.command == "prepare": prepare()
    elif args.command == "bundle": bundle(args.aid, args.start, args.count)
    else: sheet(args.keys, args.name)
