"""Structural registry and display of procurement units; no text classification."""
from __future__ import annotations


def package_context(package):
    get = package.get if isinstance(package, dict) else lambda key, default=None: getattr(package, key, default)
    context = {key: get(key) for key in ("package_no", "package_kind", "package_name", "source_package_no")}
    context["package_kind"] = context["package_kind"] or "unknown"
    return context


def package_label(package):
    context = package_context(package)
    if context["package_kind"] == "unpartitioned":
        return "未分包"
    return context["package_name"] or None


def registry_references(rows):
    """Allocate once. Preserve unambiguous old references; never silently drop duplicate numbers."""
    raw = [str(row.get("package_no") or "").strip() for row in rows]
    reserved = set(raw) | {"announcement", "unknown"}
    used = set()
    for index, (row, number) in enumerate(zip(rows, raw)):
        kind = row.get("package_kind", "unknown")
        # Namespaces distinguish units inside this announcement. They are not business keys.
        typed = kind == "lot" or raw.count(number) > 1 and kind in ("contract_package", "unpartitioned")
        ref = f"{kind}:{number}" if typed and number else number
        if not ref or ref in used or ref in {"announcement", "unknown"} or ref != number and ref in reserved:
            ref = f"unit:{index + 1}"
            while ref in reserved or ref in used:
                ref = "_" + ref
        used.add(ref)
        yield ref, number or None


def reference_matches(number, packages):
    """Return directory references matching an existing reference or original number.

    An ambiguous number yields all possibilities, never an arbitrary first unit.
    """
    return list(dict.fromkeys(context["package_no"] for package in packages
                              if (context := package_context(package))["package_no"] and number
                              and number in (context["package_no"], context["source_package_no"])))


def hint_reference(number, packages):
    """Resolve an existing literal-number hint only if structurally unambiguous."""
    matches = reference_matches(number, packages)
    return matches[0] if len(matches) == 1 else None
