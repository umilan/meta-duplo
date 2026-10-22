# Rebuild kit: meta-duplo / Filomena

These skills describe the app **as it is built now** (code is the source of truth), precisely enough to recreate
it from an empty repository. Each folder holds one `SKILL.md` with frontmatter (`name`, `description`), the
details (names, constants, schemas, routes, classes, keys), a **Rebuild checklist** and a **Verify** section.

They complement, not replace:

- `skills/*/SKILL.md`: the domain rules (capture date, place, device, context, confidence, duplicates),
  including parts not implemented yet. Skills 05 and 06 here say which parts the code implements.
- `docs/`: architecture, workflow, UI spec, decisions (ADRs) and the schema. Where docs and code differ, the
  skills below follow the code and say so.
- `CLAUDE.md`: the hard rules. They bind a rebuild just as they bind changes.

## Build order

| # | Skill | Build it when | Done when |
|---|---|---|---|
| 01 | [architecture-and-stack](01-architecture-and-stack/SKILL.md) | first: repo, package layout, CLI, library folder, token | `uv run meta-duplo --help` lists the commands |
| 02 | [data-model](02-data-model/SKILL.md) | with 01: SQLite schema, migrations, settings keys | a new library creates every table |
| 03 | [scan-pipeline](03-scan-pipeline/SKILL.md) | inventory → extract → hash → fingerprint → thumbnails; jobs and cancel | scanning the demo disk fills `files`, `meta`, `hashes`, `fingerprints` |
| 04 | [metadata-extraction](04-metadata-extraction/SKILL.md) | with 03: fields, dates, devices, GPS, places | demo dates, devices and places read correctly |
| 05 | [duplicate-matching](05-duplicate-matching/SKILL.md) | steps 1, 2 and 4: verdicts, keepers, result pages | `tests/test_workflow.py::test_step1/2` pass |
| 06 | [capture-resolution](06-capture-resolution/SKILL.md) | proposals for date / place / device, decisions | WhatsApp copy dated from its original |
| 07 | [file-naming](07-file-naming/SKILL.md) | patterns, tokens, column tags, collisions; the naming dialog | expected demo names; dialog checks pass |
| 08 | [plans-apply-undo](08-plans-apply-undo/SKILL.md) | the only code that writes to sources | apply + undo leaves the disk byte-identical |
| 09 | [workbench-model](09-workbench-model/SKILL.md) | catalog, rows, grouping, diffs, filters, edits, history | `tests/test_workbench.py` passes |
| 10 | [web-routes](10-web-routes/SKILL.md) | FastAPI routes, `/wb/view` actions, context, htmx conventions | every route answers as listed |
| 11 | [workbench-ui](11-workbench-ui/SKILL.md) | the Filomena screen: table, menus, dialogs, keyboard, preview, lightbox | browser checks in 13 pass |
| 12 | [design-system](12-design-system/SKILL.md) | tokens, components, themes, settings sheet | light and dark screenshots match |
| 13 | [testing-and-verification](13-testing-and-verification/SKILL.md) | throughout: demo disk, pytest, Playwright recipes | 32 tests pass, no console errors |

Port the tests early (13): they are the acceptance criteria for 03–09.

[KNOWN_ISSUES.md](KNOWN_ISSUES.md) lists the defects and rule gaps found while writing these skills
(data-safety items first). Decide each one on purpose in a rebuild rather than copying it.

## Ground rules for a rebuild (from CLAUDE.md)

These are the target rules. Today's code does not meet all of them yet; skills 02–04 mark the gaps:
ExifTool is not used yet (Pillow + pillow-heif and ffprobe read metadata, ADR-013), so there is no `SourceFile`
keying; per-file errors go to `files.error` (no `file_errors` table); `RAW_VERSION` is the only stage version
(no `STAGE_VERSION`); original path bytes are not stored. A rebuild may close these gaps, but must not
silently change behaviour that the tests in 13 pin down.

1. Never write to source folders except through `plans.apply_plan` / `undo_plan` with an approved plan; tests
   use temp copies of the demo disk.
2. Key ExifTool results by `SourceFile`; keep the integrity checks.
3. Never use fixed UTC offsets; convert with `zoneinfo` per timestamp; keep naive local times apart from UTC.
4. Every evidence candidate has an origin (independence group); copies never add votes.
5. Per-file errors never abort a run (`file_errors`).
6. Stages are idempotent and versioned.
7. No frontend build step: Jinja2 templates, htmx, Alpine.js, plain CSS with tokens on the
   2 · 4 · 8 · 16 · 24 · 32 · 40 · 48 · 64 scale; no inline styles except user data and JS-computed layout.
8. Localhost only, session token, CSRF header on POSTs.
9. Paths NFC in the DB; filesystem operations use the stored original bytes.
10. Keep it Tauri-ready: host / port / token from the environment, relative URLs, Safari-compatible UI.

## Keeping this folder current

When behaviour changes, update the matching skill here in the same commit as the code (and `docs/UI_SPEC.md`
for screens). A skill that no longer matches the code is worse than none.
