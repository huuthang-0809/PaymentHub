import { expect, test } from '@playwright/test';

test('real Grafana table fetches pages only on navigation, refreshes in place, and resets on scope changes', async ({ page }) => {
  await expect.poll(async () => {
    try { return (await page.request.get('/api/health', { timeout: 2000 })).ok(); }
    catch { return false; }
  }, { timeout: 60_000 }).toBeTruthy();
  const login = await page.request.post('/login', { data: {
    user: process.env.GRAFANA_ADMIN_USER || 'admin', password: process.env.GRAFANA_ADMIN_PASSWORD || 'admin',
  } });
  expect(login.ok()).toBeTruthy();
  const proxy = '/api/datasources/proxy/uid/reconciliation-api';
  const runsResponse = await page.request.get(`${proxy}/reconciliation-runs?status=COMPLETED&limit=10`);
  expect(runsResponse.ok()).toBeTruthy();
  const runs = (await runsResponse.json()).items;
  test.skip(!runs.length, 'Needs at least one completed reconciliation run.');
  const runId = runs[0].run_id;
  const requests: URL[] = [];
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (url.pathname.includes(`${proxy}/reconciliation-runs/`) && url.pathname.endsWith('/results')) {
      requests.push(url);
    }
  });
  const dashboardUrl = `/d-solo/paymenthub-reconciliation-api?panelId=10&var-run_id=${runId}&var-result_type=ALL`;
  await page.goto(dashboardUrl);
  await expect(page.getByText('Page 1 · 200 rows', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Previous', exact: true })).toBeDisabled();
  expect(requests).toHaveLength(1);
  expect(requests[0].searchParams.get('cursor')).toBeNull();
  await expect(page.getByText('System transaction ID', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Next', exact: true }).click();
  await expect(page.getByText('Page 2 · 200 rows', { exact: true })).toBeVisible();
  expect(requests).toHaveLength(2);
  const secondCursor = requests[1].searchParams.get('cursor');
  expect(secondCursor).toBeTruthy();
  await page.getByRole('button', { name: 'Refresh page', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Refresh page', exact: true })).toBeEnabled();
  expect(requests).toHaveLength(3);
  expect(requests[2].searchParams.get('cursor')).toEqual(secondCursor);
  await expect(page.getByText('Page 2 · 200 rows', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Previous', exact: true }).click();
  await expect(page.getByText('Page 1 · 200 rows', { exact: true })).toBeVisible();
  expect(requests).toHaveLength(4);
  expect(requests[3].searchParams.get('cursor')).toBeNull();

  // Small outcome in the real run validates end-of-data and disabled Next.
  await page.goto(dashboardUrl.replace('var-result_type=ALL', 'var-result_type=INVALID_DATA'));
  await expect(page.getByText(/^Page 1 · \d+ rows$/)).toBeVisible();
  expect(requests.at(-1)!.searchParams.get('cursor')).toBeNull();
  expect(requests.at(-1)!.searchParams.get('result')).toEqual('INVALID_DATA');
  // Return to ALL and prove navigation is not capped at the former 10,000 rows.
  await page.goto(dashboardUrl);
  await expect(page.getByText('Page 1 · 200 rows', { exact: true })).toBeVisible();
  const summary = await (await page.request.get(`${proxy}/reconciliation-runs/${runId}/summary`)).json();
  const count = Object.values(summary.summary as Record<string, number>).reduce((a, b) => a + b, 0);
  if (count > 10_200) {
    for (let index = 2; index <= 52; index++) {
      await page.getByRole('button', { name: 'Next', exact: true }).click();
      await expect(page.getByText(`Page ${index} · 200 rows`, { exact: true })).toBeVisible();
    }
    await expect(page.getByRole('button', { name: 'Next', exact: true })).toBeEnabled();
  }
  await page.screenshot({ path: 'test-results/evidence-cursor.png', fullPage: true });
  expect(errors).toEqual([]);
});

test('last-page controls, auto-refresh, and in-place Outcome changes work with bounded pages', async ({ page }) => {
  await page.request.post('/login', { data: {
    user: process.env.GRAFANA_ADMIN_USER || 'admin', password: process.env.GRAFANA_ADMIN_PASSWORD || 'admin',
  } });
  const requests: URL[] = [];
  await page.route('**/api/datasources/proxy/uid/reconciliation-api/reconciliation-runs/*/results?**', async (route) => {
    const url = new URL(route.request().url());
    requests.push(url);
    const second = url.searchParams.has('cursor');
    await route.fulfill({ contentType: 'application/json', body:
      `{"items":[{"result_key":${second ? 201 : 1},"result_type":"MATCHED","system_amount_minor":9007199254740993}],` +
      `"next_cursor":${second ? 'null' : '"second"'}}`,
    });
  });
  await page.goto('/d-solo/paymenthub-reconciliation-api?panelId=10&var-run_id=13634b83-1446-4a33-ac4f-4449990d672c&var-result_type=ALL&refresh=5s');
  await expect(page.getByText('Page 1 · 1 rows', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Next', exact: true }).click();
  await expect(page.getByText('Page 2 · 1 rows', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Next', exact: true })).toBeDisabled();
  const beforeRefresh = requests.length;
  await expect.poll(() => requests.length, { timeout: 12_000 }).toBeGreaterThan(beforeRefresh);
  expect(requests.at(-1)!.searchParams.get('cursor')).toEqual('second');
  await expect(page.getByText('Page 2 · 1 rows', { exact: true })).toBeVisible();
  // Same navigation API that dashboard variable selectors use, without reloading.
  await page.evaluate(async () => {
    const runtime = await (window as any).System.import('@grafana/runtime');
    runtime.locationService.partial({ 'var-result_type': 'MATCHED' }, true);
  });
  await expect(page.getByText('Page 1 · 1 rows', { exact: true })).toBeVisible();
  expect(requests.at(-1)!.searchParams.get('cursor')).toBeNull();
  expect(requests.at(-1)!.searchParams.get('result')).toEqual('MATCHED');
});
