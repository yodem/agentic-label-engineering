# Phase 4 Implementation Plan

> **For agentic workers:** use superpowers:executing-plans to implement this plan task by task.

**Goal:** Read items from the library through the new reader and index them.

---

### Task 1: Reader client

**Files:**
- Create: `src/reader/client.py`
- Modify: `src/reader/__init__.py:1-12`
- Test: `tests/reader/test_client.py`

- [ ] **Step 1: Write the failing test**

Run: `python -m pytest tests/reader/test_client.py -q`

### Task 2: Index builder

**Files:**
- Create: `src/index/builder.py`
- Test: `tests/index/test_builder.py`

Depends on Task 1.

- [ ] **Step 1: Write the failing test**

Run: `python -m pytest tests/index/test_builder.py -q`

### Task 3: Docs

**Files:**
- Modify: `docs/reader.md`
