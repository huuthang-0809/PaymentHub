import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseEvidencePage } from '../src/response';

test('monetary JSON integers retain all digits and row numbers remain numeric', () => {
  const page = parseEvidencePage(`{"items":[{"result_key":1,"source_row_number":200,
    "system_amount_minor":9007199254740993,"provider_amount_minor":9223372036854775807,
    "delta_amount":-9007199254740993,"refunded_amount_minor":null,
    "successful_child_amount_minor":9007199254740993,"parent_refunded_amount_minor":9223372036854775807}],"next_cursor":"next"}`);
  assert.deepEqual(page.items[0], {
    result_key: 1, source_row_number: 200, system_amount_minor: '9007199254740993',
    provider_amount_minor: '9223372036854775807', delta_amount: '-9007199254740993', refunded_amount_minor: null,
    successful_child_amount_minor: '9007199254740993', parent_refunded_amount_minor: '9223372036854775807',
  });
  assert.equal(page.next_cursor, 'next');
});

test('malformed pages fail explicitly rather than producing an empty success', () => {
  assert.throws(() => parseEvidencePage('{"items":[]}'), /invalid page/);
  assert.throws(() => parseEvidencePage('{"items":[],"next_cursor":200}'), /invalid page/);
  assert.throws(() => parseEvidencePage('null'), /invalid page/);
});
