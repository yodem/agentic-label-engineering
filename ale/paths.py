"""Where ALE's non-Python runtime files live, and what to put on PYTHONPATH.

A checkout (an editable install, or the Claude Code plugin) is itself the
plugin directory: ``.claude-plugin/``, ``hooks/``, ``agents/``, ``catalog/``
and ``bin/`` sit beside the ``ale`` package. A wheel carries a copy of that
plugin tree in ``ale/_bundle`` (see ``setup.py``).
"""
from __future__ import annotations

import os
from typing import Optional

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))


def import_root(package_dir: Optional[str] = None) -> str:
    """The directory that must be on PYTHONPATH for ``python -m ale``."""
    return os.path.dirname(package_dir or PACKAGE_DIR)


def is_plugin_dir(path: str) -> bool:
    return (os.path.isfile(os.path.join(path, ".claude-plugin", "plugin.json"))
            and os.path.isfile(os.path.join(path, "bin", "ale-spawn")))


def plugin_root(package_dir: Optional[str] = None) -> str:
    """The plugin directory: the checkout when there is one, else the bundled copy."""
    package_dir = package_dir or PACKAGE_DIR
    checkout = import_root(package_dir)
    if is_plugin_dir(checkout):
        return checkout
    return os.path.join(package_dir, "_bundle")
