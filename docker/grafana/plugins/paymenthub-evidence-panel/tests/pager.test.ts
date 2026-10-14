import assert from 'node:assert/strict';
import { test } from 'node:test';
import { CursorPager, type EvidencePage } from '../src/pager';

test('next and previous load cursors on demand and retain only the current page', async () => {
  const calls: Array<string | null> = [];
  const pager = new CursorPager(async (cursor) => {
    calls.push(cursor);
    return cursor === null
      ? { items: [{ result_key: 1 }], next_cursor: 'second' }
      : { items: [{ result_key: 2 }], next_cursor: null };
  });
  assert.deepEqual(calls, []);
  await pager.refresh();
  assert.deepEqual(calls, [null]);
  await pager.next();
  assert.deepEqual(pager.getSnapshot().items, [{ result_key: 2 }]);
  assert.equal(pager.getSnapshot().pageIndex, 1);
  await pager.next();
  assert.deepEqual(calls, [null, 'second']);
  await pager.previous();
  assert.deepEqual(calls, [null, 'second', null]);
  assert.equal(pager.getSnapshot().pageIndex, 0);
  await pager.previous();
  assert.equal(calls.length, 3);
});

test('refresh reloads the current page without returning to the first page', async () => {
  const calls: Array<string | null> = [];
  const pager = new CursorPager(async (cursor) => {
    calls.push(cursor);
    return { items: [{ result_key: cursor ? 201 : 1 }], next_cursor: 'second' };
  });
  await pager.refresh();
  await pager.next();
  await pager.refresh();
  assert.deepEqual(calls, [null, 'second', 'second']);
  assert.equal(pager.getSnapshot().pageIndex, 1);
});

test('a failed navigation preserves the previous page and can be retried', async () => {
  let fail = true;
  const pager = new CursorPager(async (cursor) => {
    if (cursor && fail) { throw new Error('temporarily unavailable'); }
    return { items: [{ result_key: cursor ? 2 : 1 }], next_cursor: cursor ? null : 'second' };
  });
  await pager.refresh();
  await pager.next();
  assert.equal(pager.getSnapshot().pageIndex, 0);
  assert.deepEqual(pager.getSnapshot().items, [{ result_key: 1 }]);
  assert.equal(pager.getSnapshot().error, 'temporarily unavailable');
  fail = false;
  await pager.next();
  assert.equal(pager.getSnapshot().pageIndex, 1);
  assert.equal(pager.getSnapshot().error, null);
});

test('overlapping requests are suppressed', async () => {
  let resolve!: (page: EvidencePage) => void;
  let calls = 0;
  const pager = new CursorPager(() => { calls++; return new Promise((done) => { resolve = done; }); });
  const pending = pager.refresh();
  await pager.refresh();
  assert.equal(calls, 1);
  resolve({ items: [], next_cursor: null });
  await pending;
  assert.equal(pager.getSnapshot().loading, false);
});

test('unmount aborts requests and ignores stale responses, including after remount', async () => {
  const pending: Array<{ signal: AbortSignal; resolve: (page: EvidencePage) => void }> = [];
  const pager = new CursorPager((_cursor, signal) => new Promise((resolve) => { pending.push({ signal, resolve }); }));
  const old = pager.refresh();
  pager.dispose();
  assert.equal(pending[0].signal.aborted, true);
  const current = pager.refresh();
  pending[1].resolve({ items: [{ result_key: 2 }], next_cursor: null });
  await current;
  pending[0].resolve({ items: [{ result_key: 1 }], next_cursor: 'stale' });
  await old;
  assert.deepEqual(pager.getSnapshot().items, [{ result_key: 2 }]);
  assert.equal(pager.getSnapshot().next_cursor, null);
});
