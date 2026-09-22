from __future__ import annotations

import hashlib
import os
from typing import Dict, List, Optional, Tuple

from .frontmatter import FrontmatterError, parse_frontmatter
from .validate import load_schema, validate


class CatalogError(ValueError):
    pass


PHASES = ("plan", "design", "implement", "test", "review", "deploy", "operate", "maintain")
MODEL_TIERS = ("cheap", "standard", "frontier")
HARNESS_MARKER = "<!-- harness: claude-code -->"


def split_body(text: str) -> Tuple[str, str]:
    lines = text.splitlines(True)
    markers = [index for index, line in enumerate(lines)
               if line.rstrip("\r\n") == HARNESS_MARKER]
    if len(markers) > 1:
        raise CatalogError("agent body contains harness marker more than once")
    if not markers:
        return text, ""
    marker_index = markers[0]
    return "".join(lines[:marker_index]), "".join(lines[marker_index + 1:])


def _agent_key(relative: str, frontmatter: dict, path: str) -> str:
    parts = relative.replace(os.sep, "/").split("/")
    filename = os.path.basename(relative)
    stem = os.path.splitext(filename)[0]
    is_default = len(parts) == 2 and stem == "_default"
    if len(parts) == 1 and stem == "general":
        expected_role, expected_sub, key = "general", "general", "general"
    elif len(parts) == 2 and parts[0] == "_cross":
        expected_role, expected_sub, key = "_cross", stem, "_cross/" + stem
    elif len(parts) == 2:
        expected_role, expected_sub, key = parts[0], stem, parts[0] + "/" + stem
    else:
        raise CatalogError("%s: agent file must be general.md or one directory deep" % path)
    if frontmatter.get("role") != expected_role:
        raise CatalogError("%s: role must match containing directory" % path)
    if is_default:
        declared_sub = frontmatter.get("sub")
        if declared_sub not in (None, "_default"):
            raise CatalogError(
                "%s: a _default.md must not declare sub '%s'" % (path, declared_sub)
            )
        frontmatter["sub"] = "_default"
        return key
    if frontmatter.get("sub") != expected_sub:
        raise CatalogError("%s: sub must match file stem" % path)
    return key


def load_catalog(roots: List[str]) -> Dict[str, dict]:
    catalog: Dict[str, dict] = {}
    schema = load_schema("agent.schema.json")
    for root in roots:
        if not os.path.isdir(root):
            continue
        real_root = os.path.realpath(root)
        root_entries: Dict[str, dict] = {}
        for directory, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for filename in sorted(filenames):
                if not filename.endswith(".md"):
                    continue
                path = os.path.abspath(os.path.join(directory, filename))
                real_path = os.path.realpath(path)
                try:
                    contained = os.path.commonpath((real_root, real_path)) == real_root
                except ValueError:
                    contained = False
                if not contained:
                    raise CatalogError("%s: agent file resolves outside catalog root %s" % (path, root))
                relative = os.path.relpath(path, root)
                try:
                    with open(path, encoding="utf-8", newline="") as handle:
                        source = handle.read()
                    frontmatter, body = parse_frontmatter(source)
                    core, harness = split_body(body)
                except (OSError, UnicodeError, FrontmatterError, CatalogError) as exc:
                    raise CatalogError("%s: %s" % (path, exc))
                errors = validate(frontmatter, schema)
                if errors:
                    raise CatalogError("%s: invalid agent %s:\n  %s" % (
                        path, frontmatter.get("name", "<unnamed>"), "\n  ".join(errors)
                    ))
                if any(read == "" for read in frontmatter["reads"]):
                    raise CatalogError("%s: reads entries must not be empty" % path)
                key = _agent_key(relative, frontmatter, path)
                if key in root_entries:
                    raise CatalogError("%s: duplicate catalog key %s in %s" % (path, key, root))
                digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
                entry = dict(frontmatter)
                entry["rules"] = {
                    key: entry["rules"].get(key, [])
                    for key in ("deny_paths", "deny_tools", "require_before_submit")
                }
                entry.update({"path": path, "sha256": digest, "core": core, "harness": harness})
                root_entries[key] = entry
        for key, entry in root_entries.items():
            if key not in catalog:
                catalog[key] = entry
    return catalog


def resolve_agent(catalog: Dict[str, dict], role: str, sub: Optional[str], phase: str) -> dict:
    if role == "infra":
        role = "devops"
        sub = "infra"
    if sub is not None:
        exact_key = "%s/%s" % (role, sub)
        exact = catalog.get(exact_key)
        if exact is not None:
            matched = "exact" if phase in exact["phases"] else "phase-mismatch"
            return _agent_ref(exact_key, exact, matched)
        cross_key = "_cross/%s" % sub
        cross = catalog.get(cross_key)
        if cross is not None:
            return _agent_ref(cross_key, cross, "exact")
    role_default_key = "%s/_default" % role
    if role_default_key in catalog:
        return _agent_ref(role_default_key, catalog[role_default_key], "role-default")
    if "general" in catalog:
        return _agent_ref("general", catalog["general"], "general")
    raise CatalogError("no agent resolved for role=%s sub=%s phase=%s" % (role, sub, phase))


def _agent_ref(key: str, agent: dict, matched: str) -> dict:
    return {
        "key": key,
        "name": agent["name"],
        "path": agent["path"],
        "sha256": agent["sha256"],
        "version": agent["version"],
        "matched": matched,
        "model_tier_min": agent["model_tier_min"],
    }
