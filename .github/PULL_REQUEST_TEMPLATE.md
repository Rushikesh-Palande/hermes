## Summary

<!--
One paragraph: WHAT this PR changes and WHY.
If WHAT is obvious from the diff, spend the paragraph on WHY.
-->

## Linked issue / phase

<!-- Example: Refs #42 · Closes #57 · Phase 3 task 4 -->

## Change type

<!-- Pick one. Multiple means split the PR. -->

- [ ] `feat` — user-visible new capability
- [ ] `fix` — bug fix
- [ ] `refactor` — internal restructure, no behaviour change
- [ ] `perf` — measurable performance improvement
- [ ] `test` — added/changed tests only
- [ ] `docs` — docs-only change
- [ ] `build` / `ci` / `chore` — tooling, deps, infra

## Behavioural parity check

<!--
If this PR touches ingest, detection, workers, or the events schema,
the default expectation is BYTE-IDENTICAL behaviour vs. the legacy system.
-->

- [ ] No change to ingest / detection / events — parity not applicable.
- [ ] Parity preserved — golden-traffic diff is clean on the attached capture.
- [ ] Parity intentionally broken — references `BUG_DECISION_LOG.md` entry #___
      and adds a matching suppression in `tests/golden/allowed_differences.yaml`.

## Reviewer test plan

<!-- Bullet list a reviewer can follow to verify locally. Keep it short. -->

- [ ]
- [ ]

## Quality gates

- [ ] `uv run ruff check` clean
- [ ] `uv run mypy services/hermes` clean
- [ ] `uv run pytest` passes; coverage on changed files ≥ 90 %
- [ ] `pnpm --prefix ui typecheck` clean
- [ ] `pnpm --prefix ui lint` clean
- [ ] If UI changed: Playwright E2E green · axe-core 0 violations · screenshot attached
- [ ] If schema changed: migration has a matching `.down.sql` or an explanatory comment
- [ ] `CHANGELOG.md` updated under `## [Unreleased]`
- [ ] New dependencies justified and minimal

## Notes for reviewer

<!-- Anything non-obvious: tradeoffs, follow-ups, known limitations. -->
