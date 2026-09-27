---
name: agent-handbook
description: Build or refresh one agent-readable handbook from your own document library, with a chapter per ALE agent, and write the refs file that points each agent prompt at its chapter. Re-runnable, and updates chapters in place.
---

# Agent handbook

ALE ships short agent definitions in `agents/`. This skill gives each of them depth
without putting anything personal in the repository: it builds one handbook in your
document store (CandleKeep, or any store with a command that prints one page of a book
by id and page), then writes a refs file. ALE reads that file at dispatch and adds a
"Deep reference (read on demand; not pasted)" section to the agent's prompt, naming
the chapter and the command that reads it. Nothing is fetched at render time.

## Inputs

Ask for these once, and keep them in the summary at the end:

- **Store read command.** The command that prints one page, written with `{book}` and
  `{page}` placeholders. If the store is CandleKeep, run `ck --help` to find the
  subcommand that reads one page of a book by id and page. Do not guess it.
- **Book.** An existing handbook id to update, or none to create one.
- **Refs file path.** Default `${XDG_CONFIG_HOME:-$HOME/.config}/ale/refs.json`. It stays
  outside every repository. Point ALE at it with `ALE_REFS_FILE=<path>` or with
  `"refs_file": "<path>"` in the roster.

## Procedure

1. **Skeleton from `agents/`.** List the agent files with
   `python3 -m ale agents list --json` (or read `agents/` directly). Each agent's key is
   its catalog key: `general`, `<role>/<sub>` such as `backend/api`, `<role>/_default`,
   and `_cross/<sub>` such as `_cross/review`. Build the table of contents:
   - **Part 0: Decision matrix.** One table, domain then sub-domain to chapter: which
     chapter an agent reads for which kind of task.
   - **One Part per domain**, in this order: backend, frontend, devops, test, docs,
     cross (`_cross`), general.
   - **One chapter per agent** in its domain's Part, titled `<catalog key>: <agent name>`.
2. **Sources per sub-domain.** For each chapter, find the relevant books in the user's
   library with the store's librarian or search. Read the pages you will cite. Keep a
   list of the books each chapter draws on.
3. **Write each chapter** in the style of *Writing Books for AI Agents*:
   - it opens with the agent's directive and checklist, copied from the agent file's
     own definition (its description, the `checklist` items), so the chapter and the
     tag agree;
   - then decision rules, each with its WHY and a concrete threshold where one exists;
   - then gotchas;
   - then citations to the source books by title and page, at least two books per
     chapter where the library has them.
4. **Create or update one book.** Find existing chapters by their exact title and replace
   their pages in place. Never add a second chapter for the same catalog key. Create only
   the chapters that are missing. Part 0 is rewritten on every run.
5. **Write the refs file.** One JSON object keyed by catalog key. Every agent in
   `agents/` gets an entry; a domain key (`backend`) may stand in for agents without a
   chapter of their own:

   ```json
   {
     "backend/api": {
       "title": "<handbook title>, Part backend, chapter backend/api",
       "how_to_read": "<store read command for the chapter's first page>",
       "read_first": "<store read command for the Part 0 page>"
     }
   }
   ```

   `read_first` always points at Part 0, so an agent checks the decision matrix before
   it opens its chapter. Write the file atomically (write a temporary file, then rename).
6. **Print a summary:** the book id and title, chapters created and updated, agents
   without a dedicated chapter (covered by a domain key), the refs file path, and the line
   that wires it in (`export ALE_REFS_FILE=<path>` or the roster's `refs_file`).

## Checks before you finish

- `python3 -c "import json,sys; json.load(open(sys.argv[1]))" <refs file>` exits 0.
- Every catalog key from step 1 resolves: its own key, its `<role>/<name>` alias, or its
  domain key is in the refs file.
- Three sampled chapters cite at least two source books each.
- Running the skill again changes pages in place and adds no chapter.

## Boundaries

- Book ids, page numbers and store commands live only in the refs file and the store.
  Never write them into `agents/`, `catalog/`, the roster in a repository, or any
  tracked file.
- Do not paste chapter text into prompts or agent files; the refs entry is a pointer.
