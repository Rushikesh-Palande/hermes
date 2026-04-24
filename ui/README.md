# hermes-ui

SvelteKit + TypeScript + Tailwind + uPlot — the operator-facing dashboard.

## Scripts

```bash
pnpm install      # one-time
pnpm dev          # dev server on :5173, proxies /api to :8080
pnpm build        # production build into ./build
pnpm preview      # serve the production build locally
pnpm typecheck    # tsc --noEmit (strict, noUncheckedIndexedAccess)
pnpm check        # svelte-check (everything Svelte can statically verify)
pnpm lint         # prettier + eslint
pnpm format       # prettier --write
```

## Structure

```
src/
├── app.html               — root HTML shell
├── app.css                — Tailwind directives + shared CSS vars
├── app.d.ts               — ambient types (SvelteKit App namespace)
├── lib/                   — shared components, stores, utils
│   └── index.ts           — barrel for the $lib alias
└── routes/                — filesystem-routed pages
    ├── +layout.svelte     — root layout (sidebar + nav lands here)
    └── +page.svelte       — temporary landing page (replaced in Phase 5)
```

## Phase-1 state

Routes: landing page only. Real auth flow, device list, live dashboard,
and event history land in Phases 5–8 per the top-level `CHANGELOG.md`.

The dev server proxies `/api/*` to `http://localhost:8080` so fetches
from the browser hit the FastAPI backend without CORS setup. Start the
API with `uv run hermes-api` in a separate terminal.
