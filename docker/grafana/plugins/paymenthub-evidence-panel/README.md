# Reconciliation cursor tables

Uses the public `Table` component from `@grafana/ui` with a shared cursor pager
for Transaction evidence, Refund cross-checks, and Daily Data Quality.
Grafana packages and runtime are pinned to 13.1.6. The panel does not import
internal or unstable components. It preserves configured field overrides,
outcome colors, resizing, page-local sorting/filtering, and monetary strings.

```bash
npm ci --ignore-scripts
npm test
npm run build
```

From the repository root, build with `docker compose build api migrate grafana`,
apply the DQ index with `docker compose run --rm --no-deps migrate`, then run
`docker compose up -d --no-deps api grafana`. This restarts the API and Grafana.
The Docker image contains
only the built frontend plugin, not Node or its dependencies. The plugin is
unsigned and explicitly allowed by ID for local development.

Browser smoke test against the running stack (needs completed runs and Chromium):

```bash
npx playwright install chromium
# Supply the configured local Grafana credentials through environment variables.
npx playwright test
```

Optional environment: `GRAFANA_URL` (default `http://localhost:3000`),
`GRAFANA_ADMIN_USER`, `GRAFANA_ADMIN_PASSWORD` (default `admin`),
`PLAYWRIGHT_CHROMIUM_EXECUTABLE` (existing Chromium executable).
The test reads existing runs and performs no mutations except login.

Each navigation makes one GET through Grafana's authenticated datasource proxy.
Only the current page is retained; Previous refetches the stored cursor. Auto
refresh reloads the current page and ignores ticks while a request is running.
Changing run/outcome resets the pager and aborts stale work. An error preserves
the last successful page so navigation can be retried. Page-local sorting and
column filters do not change API cursor order. Outcome is applied by the API.

The resource panel option selects the endpoint. Refunds default to 100 rows per
page and no longer come from the full summary payload; dashboard summary queries
exclude refund checks. DQ also defaults to 100 rows per page, sorted by business
date and evaluation time descending with stable run/rule/version tie breakers.
Its cursor carries an evaluation cutoff from the first page. Each finding shows
at most 20 affected keys, with the complete affected count; follow the Run ID link
to examine all evidence. Existing non-paginated DQ and full-summary APIs remain
available. Large refund monetary values are parsed losslessly before rendering.

The APIs return `next_cursor`, but no total row count; the footer therefore
shows the current page and its row count without inventing a total page count.
The Operations dashboard still uses Infinity's existing automatic pagination.