"""Where ALE's non-Python runtime files live.

A checkout (or an editable install, or the Claude Code plugin) keeps ``agents/``,
``catalog/`` and ``bin/`` beside the ``ale`` package. A wheel carries copies of
them in ``ale/_bundle`` (see ``setup.py``).
"""
from __future__ import annotations

import os
from typing import Optional

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))


def plugin_root(package_dir: Optional[str] = None) -> str:
    package_dir = package_dir or PACKAGE_DIR
    checkout = os.path.dirname(package_dir)
    if os.path.isdir(os.path.join(checkout, "agents")):
        return checkout
    return os.path.join(package_dir, "_bundle")
