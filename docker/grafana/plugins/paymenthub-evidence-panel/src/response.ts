import { isLosslessNumber, parse } from 'lossless-json';
import type { EvidencePage } from './pager';

export function parseEvidencePage(text: string): EvidencePage {
  // JSON monetary integers can exceed Number.MAX_SAFE_INTEGER. Parse their
  // original tokens as strings before any JavaScript number conversion.
  const money = new Set(['system_amount_minor', 'provider_amount_minor', 'refunded_amount_minor', 'delta_amount',
    'successful_child_amount_minor', 'parent_refunded_amount_minor']);
  const page = parse(text, (key, value) => isLosslessNumber(value)
    ? money.has(key) ? value.toString() : Number(value.toString())
    : value) as EvidencePage;
  if (!page || !Array.isArray(page.items) ||
      !(page.next_cursor === null || typeof page.next_cursor === 'string')) {
    throw new Error('The reconciliation API returned an invalid page.');
  }
  for (const row of page.items) {
    for (const key of ['result_key', 'source_row_number']) {
      if (row[key] !== null && row[key] !== undefined) {
        const value = Number(row[key]);
        if (!Number.isSafeInteger(value)) {
          throw new Error('The reconciliation API returned an invalid row number.');
        }
        row[key] = value;
      }
    }
  }
  return page;
}
