# Feature spec: <name>

> Copy this file to `docs/features/NNN-<slug>.md`. Keep it short: one page is ideal.

## 1. Problem
What hurts today? Which files or situations? A real example from the archive (path, what is
wrong).

## 2. Goal / success criteria
- Measurable outcome (e.g. "≥ 90% of MTS videos get time confidence ≥ high at the day level").
- What must **not** get worse (regressions to watch).

## 3. Scope
- In:
- Out:

## 4. Related skill sections
- e.g. `media-capture-date-resolver` §9.7 (QuickTime convention), `media-context-resolver` B1.
- If logic changes, **update the skill first** and link the diff.

## 5. Design
- Stage(s) touched; new or changed tables/columns (with a migration number).
- Algorithm sketch (pseudocode).
- Confidence impact: new sources/heuristics, their `origin` and default reliability.
- UI: screens or partials affected; a sketch or HTML snippet.

## 6. Data and fixtures
- New fixture files needed (`tests/fixtures/...`) and their `expected.yaml`.
- Masking-calibration bucket(s) to add.

## 7. Safety
- Does it write to sources? If yes: plan/apply path, undo, verification.
- Privacy (GPS, serials, faces): what is stored, what is shown.

## 8. Performance
- Expected cost on 500k files; does it need disk reads? Can it reuse `meta_raw`?

## 9. Rollout
- Stage version bump? Which results become stale and need re-running?
- Feature flag / optional module?

## 10. Open questions
-
