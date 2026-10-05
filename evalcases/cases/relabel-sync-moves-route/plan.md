# Relabel sync plan

### Task 1: First
```ale-label
{
 "task_id": "T1",
 "title": "First",
 "labels": {"role":"backend","model_tier":"standard","risk":"low","effort":"S","lane":"inline","locality":"any","phase":"implement"},
 "lane_reason": "The human stays available.",
 "acceptance": [
  {"id":"A1","cmd":"python -m pytest tests/test_a.py -q","expect":"exit0"},
  {"id":"A2","cmd":"true","expect":"exit0"}
 ],
 "allowed_paths": ["src/a.py"],
 "depends_on": [],
 "worktree": "per_task",
 "assignments": [
  {"kind":"executor","role":"backend","model_tier":"standard","executor":null,"trigger":"ready"}
 ],
 "route": {"harness":"claude","model":"claude-sonnet-5","mode":"pane"}
}
```

**Files:**
- Create: `src/a.py`

Run: `python -m pytest tests/test_a.py -q`

### Task 2: Second
```ale-label
{
 "task_id": "T2",
 "title": "Second",
 "labels": {"role":"backend","model_tier":"standard","risk":"low","effort":"S","lane":"inline","locality":"any","phase":"implement"},
 "lane_reason": "The human stays available.",
 "acceptance": [
  {"id":"A1","cmd":"python -m pytest tests/test_b.py -q","expect":"exit0"},
  {"id":"A2","cmd":"true","expect":"exit0"}
 ],
 "allowed_paths": ["src/b.py"],
 "depends_on": [],
 "worktree": "per_task",
 "assignments": [
  {"kind":"executor","role":"backend","model_tier":"standard","executor":null,"trigger":"ready"}
 ],
 "route": {"harness":"claude","model":"claude-sonnet-5","mode":"pane"}
}
```

**Files:**
- Create: `src/b.py`

Run: `python -m pytest tests/test_b.py -q`
