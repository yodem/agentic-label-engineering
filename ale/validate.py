from __future__ import annotations

import json
import os
import re
from typing import Any, List

SCHEMA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema")

_SUPPORTED = {
    "type", "enum", "const", "required", "properties", "additionalProperties",
    "items", "minItems", "maxItems", "minLength", "maxLength", "pattern",
    "minimum", "anyOf", "title", "description", "$schema", "$id",
}


def load_schema(name: str) -> dict:
    with open(os.path.join(SCHEMA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def _is_type(value: Any, name: str) -> bool:
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name == "string":
        return isinstance(value, str)
    if name == "object":
        return isinstance(value, dict)
    if name == "array":
        return isinstance(value, list)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "null":
        return value is None
    raise ValueError("unsupported type name: %s" % name)


def validate(inst: Any, schema: dict, path: str = "$") -> List[str]:
    unknown = set(schema) - _SUPPORTED
    if unknown:
        raise ValueError("unsupported schema keyword(s) at %s: %s" % (path, sorted(unknown)))
    errs: List[str] = []
    if "type" in schema:
        names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(inst, n) for n in names):
            return ["%s: expected type %s" % (path, "|".join(names))]
    if "const" in schema and inst != schema["const"]:
        errs.append("%s: must equal %r" % (path, schema["const"]))
    if "enum" in schema and inst not in schema["enum"]:
        errs.append("%s: %r not in %r" % (path, inst, schema["enum"]))
    if isinstance(inst, str):
        if "minLength" in schema and len(inst) < schema["minLength"]:
            errs.append("%s: shorter than %d" % (path, schema["minLength"]))
        if "maxLength" in schema and len(inst) > schema["maxLength"]:
            errs.append("%s: longer than %d" % (path, schema["maxLength"]))
        if "pattern" in schema and re.search(schema["pattern"], inst) is None:
            errs.append("%s: does not match %s" % (path, schema["pattern"]))
    if _is_type(inst, "number") and "minimum" in schema and inst < schema["minimum"]:
        errs.append("%s: below minimum %s" % (path, schema["minimum"]))
    if isinstance(inst, list):
        if "minItems" in schema and len(inst) < schema["minItems"]:
            errs.append("%s: fewer than %d items" % (path, schema["minItems"]))
        if "maxItems" in schema and len(inst) > schema["maxItems"]:
            errs.append("%s: more than %d items" % (path, schema["maxItems"]))
        if "items" in schema:
            for i, item in enumerate(inst):
                errs.extend(validate(item, schema["items"], "%s[%d]" % (path, i)))
    if isinstance(inst, dict):
        for key in schema.get("required", []):
            if key not in inst:
                errs.append("%s: missing required key %r" % (path, key))
        props = schema.get("properties", {})
        for key, sub in props.items():
            if key in inst:
                errs.extend(validate(inst[key], sub, "%s.%s" % (path, key)))
        if schema.get("additionalProperties") is False:
            for key in inst:
                if key not in props:
                    errs.append("%s: unexpected key %r" % (path, key))
    if "anyOf" in schema:
        if not any(validate(inst, sub, path) == [] for sub in schema["anyOf"]):
            errs.append("%s: matches none of the allowed shapes" % path)
    return errs
