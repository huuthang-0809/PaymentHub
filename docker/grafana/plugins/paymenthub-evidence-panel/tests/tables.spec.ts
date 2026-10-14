import { expect, test } from '@playwright/test';

for (const table of [
  { name: 'Refund cross-checks', dashboard: 'paymenthub-reconciliation-api', panel: 8,
    endpoint: '/reconciliation-runs/13634b83-1446-4a33-ac4f-4449990d672c/refund-cross-checks' },
  { name: 'Daily Data Quality', dashboard: 'paymenthub-reconciliation', panel: 21,
    endpoint: '/internal/data-quality/page' },
]) {
  test(`${table.name} uses one request per page and preserves current-page refresh`, async ({ page }) => {
    await expect.poll(async () => {
      try { return (await page.request.get('/api/health', { timeout: 2000 })).ok(); } catch { return false; }
    }, { timeout: 60_000 }).toBeTruthy();
    expect((await page.request.post('/login', { data: {
      user: process.env.GRAFANA_ADMIN_USER || 'admin', password: process.env.GRAFANA_ADMIN_PASSWORD || 'admin',
    } })).ok()).toBeTruthy();
    const requests: URL[] = [];
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (url.pathname.endsWith(table.endpoint)) { requests.push(url); }
    });
    const firstResponse = page.waitForResponse((response) => new URL(response.url()).pathname.endsWith(table.endpoint));
    await page.goto(`/d-solo/${table.dashboard}?panelId=${table.panel}&var-run_id=13634b83-1446-4a33-ac4f-4449990d672c`);
    const first = await (await firstResponse).json();
    await expect(page.getByText(`Page 1 · ${first.items.length} rows`, { exact: true })).toBeVisible();
    expect(first.items.length).toBeLessThanOrEqual(100);
    expect(requests).toHaveLength(1);
    await expect(page.getByRole('button', { name: 'Previous', exact: true })).toBeDisabled();
    if (first.next_cursor) {
      const nextResponse = page.waitForResponse((response) => new URL(response.url()).searchParams.has('cursor') &&
        new URL(response.url()).pathname.endsWith(table.endpoint));
      await page.getByRole('button', { name: 'Next', exact: true }).click();
      const second = await (await nextResponse).json();
      await expect(page.getByText(`Page 2 · ${second.items.length} rows`, { exact: true })).toBeVisible();
      expect(requests).toHaveLength(2);
      expect(requests[1].searchParams.get('cursor')).toEqual(first.next_cursor);
      if (table.panel === 8) {
        expect(second.items[0].check_key).toBeGreaterThan(first.items.at(-1).check_key);
      }
      await page.getByRole('button', { name: 'Refresh page', exact: true }).click();
      await expect(page.getByRole('button', { name: 'Refresh page', exact: true })).toBeEnabled();
      expect(requests).toHaveLength(3);
      expect(requests[2].searchParams.get('cursor')).toEqual(first.next_cursor);
      await expect(page.getByText(`Page 2 · ${second.items.length} rows`, { exact: true })).toBeVisible();
      await page.getByRole('button', { name: 'Previous', exact: true }).click();
      await expect(page.getByText(`Page 1 · ${first.items.length} rows`, { exact: true })).toBeVisible();
      expect(requests.at(-1)!.searchParams.get('cursor')).toBeNull();
    } else {
      await expect(page.getByRole('button', { name: 'Next', exact: true })).toBeDisabled();
    }
    if (table.panel === 21) {
      expect(first.items.every((row: any) => row.affected_result_keys.length <= 20)).toBeTruthy();
      const runLink = page.getByRole('link', { name: first.items[0].run_id, exact: true }).first();
      await expect(runLink).toHaveAttribute('href', new RegExp(`var-run_id=${first.items[0].run_id}`));
    }
    await page.screenshot({ path: `test-results/${table.panel === 8 ? 'refund-checks' : 'daily-quality'}-cursor.png`, fullPage: true });
    expect(errors).toEqual([]);
  });
}
