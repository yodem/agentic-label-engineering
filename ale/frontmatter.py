from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple


class FrontmatterError(ValueError):
    pass


_KEY = re.compile(r"^[A-Za-z0-9_.-]+$")
_INTEGER = re.compile(r"^[+-]?[0-9]+$")


def _fail(line: int, message: str) -> FrontmatterError:
    return FrontmatterError("line %d: %s" % (line, message))


def _strip_comment(value: str, line: int) -> str:
    quote = None
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if quote == '"' and char == "\\":
            escaped = True
        elif quote:
            if char == quote:
                if quote == "'" and index + 1 < len(value) and value[index + 1] == "'":
                    continue
                quote = None
        elif char in ("'", '"'):
            quote = char
        elif char == "#" and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
    if quote:
        raise _fail(line, "unterminated quoted string")
    return value.rstrip()


def _quoted(value: str, line: int) -> str:
    if len(value) < 2 or value[-1] != value[0]:
        raise _fail(line, "invalid quoted string")
    if value[0] == '"':
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            raise _fail(line, "invalid quoted string escape")
        if not isinstance(parsed, str):
            raise _fail(line, "invalid quoted string")
        return parsed
    return value[1:-1].replace("''", "'")


def _split_inline(value: str, line: int) -> List[str]:
    contents = value[1:-1].strip()
    if not contents:
        return []
    parts: List[str] = []
    start = 0
    quote = None
    escaped = False
    for index, char in enumerate(contents):
        if escaped:
            escaped = False
        elif quote == '"' and char == "\\":
            escaped = True
        elif quote:
            if char == quote:
                quote = None
        elif char in ("'", '"'):
            quote = char
        elif char == ",":
            parts.append(contents[start:index].strip())
            start = index + 1
    if quote:
        raise _fail(line, "unterminated quoted string")
    parts.append(contents[start:].strip())
    if any(not part for part in parts):
        raise _fail(line, "empty inline list item")
    return parts


def _split_mapping_item(item: str, line: int) -> Tuple[str, str]:
    quote = None
    escaped = False
    for index, char in enumerate(item):
        if escaped:
            escaped = False
        elif quote == '"' and char == "\\":
            escaped = True
        elif quote:
            if char == quote:
                quote = None
        elif char in ("'", '"'):
            quote = char
        elif char == ":":
            key, value = item[:index].strip(), item[index + 1:].strip()
            if not _KEY.match(key) or not value:
                raise _fail(line, "invalid inline mapping entry")
            return key, value
    raise _fail(line, "invalid inline mapping entry")


def _scalar(raw: str, line: int) -> Any:
    value = _strip_comment(raw.strip(), line)
    if not value:
        return ""
    if value.startswith(("'", '"')):
        return _quoted(value, line)
    if value.startswith("["):
        if not value.endswith("]"):
            raise _fail(line, "invalid inline list")
        return [_scalar(item, line) for item in _split_inline(value, line)]
    if value.startswith("{"):
        if not value.endswith("}"):
            raise _fail(line, "invalid inline mapping")
        items = _split_inline(value, line)
        result = {}
        for item in items:
            key, raw_value = _split_mapping_item(item, line)
            if key in result:
                raise _fail(line, "duplicate inline mapping key %r" % key)
            if raw_value.startswith(("{", "[")):
                raise _fail(line, "nested inline mappings are not supported")
            result[key] = _scalar(raw_value, line)
        return result
    if value in ("true", "false"):
        return value == "true"
    if value in ("null", "~"):
        return None
    if _INTEGER.match(value):
        try:
            return int(value)
        except ValueError:
            pass
    if value.startswith(("!!", "&", "*", "|", ">")):
        raise _fail(line, "unsupported YAML construct")
    if any(token in value for token in (": ", " #")):
        raise _fail(line, "unsupported YAML construct")
    return value


def parse_frontmatter(text: str) -> Tuple[dict, str]:
    lines = text.splitlines(True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return {}, text

    closing = None
    for index in range(1, len(lines)):
        if lines[index].rstrip("\r\n") == "---":
            closing = index
            break
    if closing is None:
        raise _fail(len(lines) + 1, "missing closing ---")

    records = []
    for offset, original in enumerate(lines[1:closing], 2):
        content = original.rstrip("\r\n")
        if "\t" in content[:len(content) - len(content.lstrip())]:
            raise _fail(offset, "tab indentation is not allowed")
        indent = len(content) - len(content.lstrip(" "))
        if indent % 2:
            raise _fail(offset, "indentation must use pairs of spaces")
        stripped = _strip_comment(content[indent:], offset).strip()
        if stripped:
            records.append((indent, stripped, offset))

    def parse_block(position: int, indent: int, depth: int) -> Tuple[Any, int]:
        is_list = records[position][1].startswith("- ") or records[position][1] == "-"
        if depth > 1 and not is_list:
            raise _fail(records[position][2], "nesting deeper than one mapping level")
        result: Any = [] if is_list else {}
        while position < len(records):
            current_indent, content, line = records[position]
            if current_indent < indent:
                break
            if current_indent > indent:
                raise _fail(line, "unexpected indentation")
            if is_list:
                if not (content == "-" or content.startswith("- ")):
                    raise _fail(line, "expected list item")
                item = content[1:].strip()
                if not item:
                    raise _fail(line, "empty list item")
                result.append(_scalar(item, line))
                position += 1
            else:
                if content.startswith("-"):
                    raise _fail(line, "unexpected list item")
                if ":" not in content:
                    raise _fail(line, "expected key: value")
                key, raw = content.split(":", 1)
                key = key.strip()
                if not _KEY.match(key):
                    raise _fail(line, "invalid key")
                if key in result:
                    raise _fail(line, "duplicate key %r" % key)
                raw = raw.strip()
                position += 1
                if raw:
                    result[key] = _scalar(raw, line)
                elif position < len(records) and records[position][0] > indent:
                    if records[position][0] != indent + 2:
                        raise _fail(records[position][2], "unexpected indentation")
                    child, position = parse_block(position, indent + 2, depth + 1)
                    result[key] = child
                else:
                    result[key] = {}
        return result, position

    if not records:
        data = {}
    else:
        if records[0][0] != 0:
            raise _fail(records[0][2], "unexpected indentation")
        data, consumed = parse_block(0, 0, 0)
        if consumed != len(records):
            raise _fail(records[consumed][2], "unexpected content")
        if not isinstance(data, dict):
            raise _fail(records[0][2], "frontmatter must be a mapping")
    return data, "".join(lines[closing + 1:])
