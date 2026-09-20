from __future__ import annotations

import hashlib
import random
import re
from typing import Dict, List, Optional, Pattern, Sequence, Tuple


def _digest(prefix: str, text: str) -> str:
    return prefix + hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]


def _denied(deny: Optional[Pattern], values: Sequence[object]) -> bool:
    if deny is None:
        return False
    for value in values:
        if value is not None and deny.search(str(value)):
            return True
    return False


def from_task_ledger(rows: List[dict], deny: Optional[Pattern], min_chars: int = 20,
                     exclude_task_id: Optional[Pattern] = None) -> Tuple[List[dict], dict]:
    stats = {"seen": 0, "kept": 0, "dropped_deny": 0, "dropped_task_id": 0,
             "dropped_work": 0, "dropped_short": 0, "duplicates": 0}
    out: List[dict] = []
    seen_desc = set()
    for row in rows:
        if row.get("event") != "start":
            continue
        stats["seen"] += 1
        desc = row.get("desc")
        if not isinstance(desc, str) or len(desc) < min_chars:
            stats["dropped_short"] += 1
            continue
        if str(row.get("profile")).strip().lower() == "work":
            stats["dropped_work"] += 1
            continue
        if _denied(exclude_task_id, [row.get("task"), row.get("task_id")]):
            stats["dropped_task_id"] += 1
            continue
        if _denied(deny, [desc, row.get("cwd"), row.get("task"), row.get("task_id")]):
            stats["dropped_deny"] += 1
            continue
        if desc in seen_desc:
            stats["duplicates"] += 1
            continue
        seen_desc.add(desc)
        out.append({"id": _digest("L", desc), "text": desc, "source": "ledger", "kind": "short"})
        stats["kept"] += 1
    return out, stats


def from_plan_files(files: List[Tuple[str, str]], deny: Optional[Pattern]) -> Tuple[List[dict], dict]:
    stats = {"seen": 0, "kept": 0, "dropped_deny": 0}
    out: List[dict] = []
    heading_re = re.compile(r"^### Task \d+.*$")
    for basename, text in files:
        current_heading = None
        current_lines: List[str] = []
        for line in text.splitlines():
            if heading_re.match(line):
                if current_heading is not None:
                    _add_plan_section(out, stats, basename, current_heading, current_lines, deny)
                current_heading = line
                current_lines = [line]
            elif current_heading is not None:
                current_lines.append(line)
        if current_heading is not None:
            _add_plan_section(out, stats, basename, current_heading, current_lines, deny)
    return out, stats


def _add_plan_section(out: List[dict], stats: dict, basename: str, heading: str, lines: List[str],
                      deny: Optional[Pattern]) -> None:
    text = "\n".join(lines).strip()
    stats["seen"] += 1
    if _denied(deny, [text]):
        stats["dropped_deny"] += 1
        return
    capped = text[:4000]
    clean_heading = heading.lstrip("#").strip()
    out.append({"id": _digest("P", capped), "text": capped, "source": "%s#%s" % (basename, clean_heading),
                "kind": "full"})
    stats["kept"] += 1


def stratified_sample(rows: List[dict], n: int, seed: int, full_share: float = 1 / 3) -> List[dict]:
    if n <= 0:
        return []
    rng = random.Random(seed)
    full = [r for r in rows if r.get("kind") == "full"]
    short = [r for r in rows if r.get("kind") == "short"]
    rng.shuffle(full)
    rng.shuffle(short)
    full_n = n if full_share >= 1 else int(n * full_share)
    picked = full[:full_n]
    picked.extend(short[:max(0, n - len(picked))])
    return picked[:n]


_HOME_RE = re.compile(r"/(?:Users|home)/[^/\s]+")
_ADDR_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\b")
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_SECRET_RE = re.compile(r"\b((?:api_key|apikey|token|password|secret)\s*[=:]\s*)\S{8,}", re.I)


def _redact_text(text: str, patterns: List[Tuple[str, Pattern, str]], counts: Dict[str, int]) -> str:
    out = text
    for label, pattern, replacement in patterns:
        out, n = pattern.subn(replacement, out)
        counts[label] = counts.get(label, 0) + n
    return out


def redact(rows: List[dict], extra: List[Tuple[Pattern, str]]) -> Tuple[List[dict], Dict[str, int]]:
    patterns = [
        ("home_dir", _HOME_RE, "~"),
        ("addr", _ADDR_RE, "<addr>"),
        ("ip", _IPV4_RE, "<ip>"),
        ("secret", _SECRET_RE, r"\1<redacted>"),
    ]
    patterns.extend(("extra_%d" % (i + 1), pattern, replacement) for i, (pattern, replacement) in enumerate(extra))
    counts: Dict[str, int] = dict((label, 0) for label, _, _ in patterns)
    out: List[dict] = []
    for row in rows:
        copied = dict(row)
        for key in ("text", "source"):
            value = copied.get(key)
            if isinstance(value, str):
                copied[key] = _redact_text(value, patterns, counts)
        out.append(copied)
    return out, counts
