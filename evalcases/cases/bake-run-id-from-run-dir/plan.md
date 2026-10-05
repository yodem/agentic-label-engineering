# Run-id plan

### Task 1: First

**Files:**
- Create: `src/a.py`

Run: `python -m pytest tests/test_a.py -q`

### Task 2: Second

**Files:**
- Create: `src/b.py`

Depends on Task 1.

Run: `python -m pytest tests/test_b.py -q`
