# CLAUDE.md: guidance for AI coding agents working on medorg

## What this is
A local macOS tool to resolve the real capture time, place and device of photos and videos on
large disks, with calibrated confidence, duplicate handling, and safe, undoable
reorganization. Read `docs/ARCHITECTURE.md` first.

## Source of truth
- **Domain logic**: `skills/*/SKILL.md` and `skills/*.md`. Implement what they say. If code
  must deviate, update the skill in the same change and explain why.
- **Schema**: `docs/schema.sql` (migration 0001) plus `medorg/db/migrations/`. Never change
  tables without a new numbered migration.
- **Decisions**: `docs/DECISIONS.md`. Don't silently reverse an ADR; add a new one.

## Stack and commands
- Python 3.12+, `uv`. FastAPI, Jinja2, htmx, Alpine.js, plain CSS. SQLite (WAL).
- External tools: `ffprobe`/`ffmpeg` (Homebrew). ExifTool comes with milestone M1 (ADR-013).
- Try it: `uv sync --extra geo`, `uv run medorg demo`, then `uv run medorg serve` (it prints a link with the session token).
- Other commands: `uv run medorg add-source <path> [--writable]`, `uv run medorg scan`.
- Test: `uv run pytest` (the end-to-end workflow on a generated demo disk, including apply + undo).
- Library: `~/MedorgLibraries/default` (`--library` or `MEDORG_LIBRARY` to change it).
- Flow: read `docs/WORKFLOW.md` (four steps) and `docs/UI_SPEC.md` before changing behaviour or screens.

## Hard rules
1. **Never write to source volumes** except through `medorg/plans.py` (`apply_plan` / `undo_plan`) with an approved plan.
   Tests must use temp copies of the fixtures.
2. **Key ExifTool results by `SourceFile`**, never by order. Keep the integrity checks
   (count, re-read empties, sample re-check).
3. **Never use fixed UTC offsets.** Convert with `zoneinfo` (IANA) per timestamp. Keep naive
   local times separate from UTC.
4. **Every candidate has an `origin`** (independence group). Copies, sidecars and inherited
   values never add independent votes.
5. **Per-file errors never abort a run.** Record them in `file_errors`.
6. **Stages are idempotent and versioned.** Bump `STAGE_VERSION` when output semantics
   change.
7. **No frontend build step.** Templates in `medorg/web/templates`, CSS in `static/css`, using
   the tokens in `tokens.css` only; no inline styles; no new JS frameworks.
8. **Localhost only**, with a session token; CSRF on POSTs.
9. Paths are NFC-normalized in the DB; filesystem operations use the stored original bytes.
10. Keep the Tauri prerequisites: host/port/token from the environment, relative asset URLs,
    Safari-compatible UI.

## Style
- Small modules, typed (`from __future__ import annotations`, type hints), dataclasses for
  records.
- SQL in `.sql` files or clearly named constants; parameterized queries only.
- Log with structured context (`stage`, `file_id`, `run_id`).

## When unsure
Prefer asking (or leaving a `TODO(spec):` with a question) over guessing domain rules. Wrong
dates or places applied to thousands of files are the main risk.
