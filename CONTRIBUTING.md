# Contributing to HERMES

Thanks for reading this before cutting a branch. HERMES is a small, opinionated
codebase with a strict quality bar; following the process below keeps it that way.

---

## 1. Branching model

We use a **protected-main, feature-branch** flow with an integration branch:

```
main                  ← production-ready; protected; tagged releases live here
  ▲
  │  (release PR, signed, CI green, CHANGELOG updated)
  │
develop               ← integration branch for merged features
  ▲
  │  (feature PR, CI green, 1+ approvals)
  │
feature/<phase>-<slug>    e.g. feature/phase-2-mqtt-ingestion
hotfix/<slug>             cut from main for emergency fixes
release/vX.Y.Z            cut from develop for release prep
```

**Rules**

- Never push directly to `main` or `develop`. All merges go through a pull request.
- Feature branches live a maximum of **2 weeks**. Longer-running work gets split.
- Hotfixes merge to `main` AND are cherry-picked back to `develop` in the same PR series.
- Release branches freeze feature work; only bug fixes, CHANGELOG updates, and version bumps.

---

## 2. Commit messages

[Conventional Commits 1.0](https://www.conventionalcommits.org/). One concern per commit.

```
<type>(<scope>): <subject, imperative, ≤ 72 chars>

<body: WHY the change, wrapped at 72 chars>

<footer: BREAKING CHANGE, Refs #, Co-authored-by>
```

**Types:** `feat`, `fix`, `docs`, `test`, `refactor`, `build`, `ci`, `chore`, `style`, `perf`, `revert`.
**Scopes:** `api`, `ingest`, `db`, `ui`, `detect`, `auth`, `pkg`, `session`, `ops`, or a migration file name.

Examples:

```
feat(detect): implement Type A O(1) running-sum variance

Port the incremental sliding-window approach from the legacy
EventDetector, reducing per-sample CPU from O(n) to O(1).
Preserves exact CV% values; golden-traffic diff passes.

Refs #42
```

```
fix(ingest): re-anchor STM32 timestamp on > 5s drift

Without this, a counter reset on the STM32 causes the x-axis
to slide into the past and current data renders off-screen.

Refs #17
```

A commit that diverges intentionally from legacy behaviour **must** reference a
`docs/contracts/BUG_DECISION_LOG.md` entry:

```
fix(detect): reject upper_threshold <= lower_threshold at config save

Previously the backend silently rewrote upper = lower + 0.01,
causing events to fire under an artificial band. Now returns 400.

Refs BUG_DECISION_LOG #18
```

---

## 3. Pull requests

**Checklist (enforced by PR template)**

- [ ] Linked issue or Phase plan section (e.g. "Phase 3 task 4").
- [ ] CI green: `ruff`, `mypy`, `pytest` (≥ 90 % coverage on changed files), `pnpm typecheck`, Playwright E2E.
- [ ] No new `console.error`, no new `warnings` in `pytest -W error`.
- [ ] CHANGELOG entry under `## [Unreleased]`.
- [ ] If touching detection logic: golden-traffic diff is clean OR a suppression is added with rationale.
- [ ] If touching the schema: migration file with matching reverse `DOWN` script.
- [ ] If touching the UI: screenshots + axe-core report attached.

**Reviewer guidance**

- Prefer one small PR to one big one. < 400 lines changed is ideal.
- The author squashes obvious fixup commits before requesting review.
- At least **one approval** on feature → develop. At least **two** on release → main.

---

## 4. Code style

**Python**

- `ruff format` on save; `ruff check` must pass.
- `mypy --strict` must pass. No `Any`, no implicit `None`, no unchecked index access.
- No bare `except`. No logging of secrets. No `print()` in service code.

**TypeScript / Svelte**

- `prettier` on save; `eslint` must pass.
- `tsc --noEmit` with `strict: true`, `noUncheckedIndexedAccess: true`.
- No `any`. Use `unknown` and narrow.
- Component files are kebab-case; exports are PascalCase.

**SQL**

- Uppercase keywords, lowercase identifiers.
- Every migration has a companion `0NNN_<slug>.down.sql` (or a comment explaining why down is infeasible).
- Changes to `events` schema require a `BUG_DECISION_LOG` entry or design note.

---

## 5. Tests

- **Unit:** `pytest` in `tests/unit/`. Fast (< 1 s per test), deterministic, no DB.
- **Integration:** `tests/integration/` — hit a real Postgres/Mosquitto via `docker-compose.test.yml`.
- **Golden traffic:** `tests/golden/` — real recorded MQTT captures; run on every PR that touches detection or ingest.
- **Playwright:** `tests/e2e/` — operator workflows end-to-end on the built UI.
- **Performance:** `tests/perf/` — soak tests; run on nightly CI, not every PR.

Tests that mock databases, brokers, or I/O will be rejected unless there is a concrete reason.

---

## 6. Reviewing a PR

As a reviewer, ask in order:

1. **Is this the right problem?** (Links to issue or phase plan.)
2. **Is this the right fix?** (Considered simpler alternatives?)
3. **Does it break behaviour parity?** (Check BUG_DECISION_LOG.)
4. **Is it testable, and are the tests real?** (Mock-free integration wherever possible.)
5. **Does it carry its weight in complexity?** (Dead code, speculative abstractions, unnecessary configurability.)

---

## 7. Releases

Tags are created by the release PR, not by hand.

```
# On a release branch (e.g. release/v0.2.0):
# 1. Update CHANGELOG.md, moving [Unreleased] entries under [0.2.0] - <date>.
# 2. Bump version in pyproject.toml and ui/package.json.
# 3. Open release PR to main.
# 4. On merge, CI tags the commit and creates a GitHub Release from the CHANGELOG section.
```

Versioning follows [SemVer](https://semver.org/) with pre-release suffixes until `v1.0.0`:
`v0.1.0-alpha.1` → `v0.1.0-beta.1` → `v0.1.0-rc.1` → `v0.1.0` → `v0.1.1` → ...

---

## 8. Getting help

- Design questions: open a **Discussion**, not an issue.
- Bugs: open an **Issue** using the bug report template.
- Security: see [`SECURITY.md`](./SECURITY.md) — **never** a public issue.
- Pair programming or architecture review: `cbidap@embedsquare.com`.
