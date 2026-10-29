# ADR 0006 — Serve the static site from the Worker instead of Cloudflare Pages

- **Status:** Accepted
- **Date:** 2026-10-30
- **Phase:** 5 (deployment)

## Context

The site has always built to static files and the plan was to host them on Cloudflare Pages.
Deployment was blocked on credentials, not on code, so the first task was to establish exactly
what the supplied token can do:

| Credential | `user/tokens/verify` | Workers API | Pages API |
|---|---|---|---|
| `CLOUDFLARE_API_TOKEN` (`cfut_…`) | 200, active | 200 | **403** |
| `TOKEN_VALUE` (`cfat_…`) | **401** | 401 | 401 |

The `cfat_` token is not a Cloudflare API token at all — it fails verification everywhere. The
`cfut_` token is valid but its policy does not include Cloudflare Pages. A Pages deploy
therefore returns 403 before any file is uploaded. Fixing that needs either a new token with
`Cloudflare Pages: Edit`, or connecting the repository through the dashboard.

The project brief permits Workers as well as Pages, and the same token already deploys the
Worker successfully. Cloudflare's Workers Static Assets feature serves a directory of files
from the same Worker and needs only `Workers: Edit`.

## Decision

Serve `web/dist` as Workers Static Assets from `colcheck-api`.

```jsonc
"assets": { "directory": "../web/dist", "binding": "ASSETS" }
```

Asset matching happens before the Worker script. Anything that is not a file (`/health`,
`/api/coverage`, `/api/ask`) falls through to the existing handler in `src/index.ts`.

## Consequences

**Good**

- The site is live without a new credential: <https://colcheck-api.jerkfish123.workers.dev>.
- The site and the API share an origin, so CORS cannot be a failure mode in production.
- One deployment covers both, and the free-plan static-asset limits are far above 1.5 MB.
- The API Worker is exercised by every visit to the site, so a broken deploy is noticed
  immediately rather than only by a separate uptime check.

**Bad**

- No per-pull-request preview deployments. Pages provides those natively; switching back
  would restore them.
- The deploy order matters: `web/dist` must be rebuilt before `wrangler deploy`, or the
  previous build is served with the new API.
- The hostname is `colcheck-api`, which now serves more than an API. Renaming the Worker
  would break the URL, so the name stays.
- Cloudflare caches 404s from a previous deployment at the edge for a short time. The first
  requests to `/` and `/ask/` after the switch returned the old 404 before the cache expired —
  worth remembering when a deploy "did not work".

## If Pages becomes available

Nothing in the site depends on Workers Assets. A token with `Cloudflare Pages: Edit` (or the
dashboard GitHub integration) would let `web.yml` deploy `web/dist` to Pages and this ADR be
superseded; the Worker would then revert to an API-only role.
