import json
import re
import subprocess
from pathlib import Path

from ale.cli import main

PLAN = """# P

## Task 1: thing

```ale-label
{"task_id": "T1", "labels": {"role": "backend", "effort": "M"}}
```

**Files:**
- Modify: `src/a.py`

Run: `true`
"""


def _bake(tmp_path, plan_text):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    plan = tmp_path / "plan.md"
    plan.write_text(plan_text, encoding="utf-8")
    roster = tmp_path / "roster.json"
    roster.write_text((Path(__file__).parents[1] / "examples" / "roster.json").read_text())
    main(["plan", "bake", str(plan), "--no-judge", "--write", "--roster", str(roster)])
    text = plan.read_text(encoding="utf-8")
    return json.loads(re.search(r"```ale-label\n(.*?)\n```", text, re.S).group(1))


def test_a_partial_block_keeps_the_parsed_paths_and_checks(tmp_path):
    block = _bake(tmp_path, PLAN)
    assert block["allowed_paths"] == ["src/a.py"]
    assert [check["cmd"] for check in block["acceptance"]] == ["true"]
    assert block["labels"]["effort"] == "M"


def test_a_block_that_states_empty_paths_keeps_them_empty(tmp_path):
    plan = PLAN.replace('"effort": "M"}}', '"effort": "M"}, "allowed_paths": [], "acceptance": []}')
    block = _bake(tmp_path, plan)
    assert block["allowed_paths"] == []
    assert block["acceptance"] == []
