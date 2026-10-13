import React, { useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import {
  applyFieldOverrides, FieldMatcherID, FieldType, PanelPlugin, toDataFrame,
  type PanelProps,
} from '@grafana/data';
import { config, RefreshEvent } from '@grafana/runtime';
import { Alert, Button, Select, Table, useTheme2 } from '@grafana/ui';
import { CursorPager } from './pager';
import { parseEvidencePage } from './response';

interface Column { selector: string; text: string; type: 'number' | 'string' | 'timestamp'; }
interface Options {
  resource?: 'evidence' | 'refunds' | 'data-quality';
  datasourceUid: string;
  runId: string;
  outcome: string;
  pageSize: number;
  columns: Column[];
}

function EvidencePanel(props: PanelProps<Options>) {
  const resource = props.options.resource || 'evidence';
  const runId = resource === 'data-quality' ? '' : props.replaceVariables(props.options.runId).trim();
  const outcome = resource === 'evidence' ? props.replaceVariables(props.options.outcome).trim() : '';
  // A scope change unmounts the old pager immediately and aborts its request.
  return <EvidenceTable key={JSON.stringify([resource, runId, outcome, props.options.datasourceUid, props.options.pageSize])}
    {...props} runId={runId} outcome={outcome} />;
}

function EvidenceTable(props: PanelProps<Options> & { runId: string; outcome: string }) {
  const { width, height, options, runId, outcome, fieldConfig, replaceVariables, eventBus } = props;
  const theme = useTheme2();
  const resource = options.resource || 'evidence';
  const tableName = resource === 'refunds' ? 'Refund cross-checks'
    : resource === 'data-quality' ? 'Daily Data Quality' : 'Transaction evidence';
  const [pageSize, setPageSize] = useState(options.pageSize || 200);
  const pager = useMemo(() => new CursorPager(async (cursor, signal) => {
    if (resource !== 'data-quality' && !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(runId)) {
      throw new Error('Select a valid Run ID to view this table.');
    }
    const query = new URLSearchParams({ limit: String(pageSize) });
    if (resource === 'evidence') { query.set('result', outcome || 'ALL'); }
    if (cursor) { query.set('cursor', cursor); }
    const path = resource === 'data-quality' ? '/internal/data-quality/page'
      : `/reconciliation-runs/${encodeURIComponent(runId)}/${resource === 'refunds' ? 'refund-cross-checks' : 'results'}`;
    // Use Grafana's authenticated same-origin data proxy, never a browser API URL.
    const response = await fetch(
      `${getBackendSrvBase()}/api/datasources/proxy/uid/${encodeURIComponent(options.datasourceUid)}` +
      `${path}?${query}`,
      { signal, credentials: 'same-origin' },
    );
    if (!response.ok) {
      const messages: Record<number, string> = {
        404: 'Run not found.', 409: 'Results are available after the run completes.',
        422: 'The query or cursor is invalid. Return to the first page.',
        503: 'The reconciliation API is unavailable. Try again.',
      };
      throw new Error(messages[response.status] || `Could not load this page (HTTP ${response.status}).`);
    }
    return parseEvidencePage(await response.text());
  }), [resource, runId, outcome, options.datasourceUid, pageSize]);
  const state = useSyncExternalStore(pager.subscribe, pager.getSnapshot);

  useEffect(() => { void pager.refresh(); return pager.dispose; }, [pager]);
  useEffect(() => {
    const subscription = eventBus.getStream(RefreshEvent).subscribe(() => { void pager.refresh(); });
    return () => subscription.unsubscribe();
  }, [pager, eventBus]);

  const frame = useMemo(() => {
    const data = toDataFrame({ name: tableName, fields: (options.columns || []).map((column) => ({
      name: column.text,
      type: column.type === 'number' ? FieldType.number : column.type === 'timestamp' ? FieldType.time : FieldType.string,
      // Monetary columns remain strings, preserving integer precision.
      values: state.items.map((row) => {
        const value = row[column.selector];
        if (value === null || value === undefined) { return null; }
        if (column.type === 'timestamp') { return Date.parse(String(value)); }
        if (column.type === 'number') { return Number(value); }
        return Array.isArray(value) ? value.join(', ') : String(value);
      }),
    })) });
    return applyFieldOverrides({ data: [data], fieldConfig, theme, replaceVariables,
      fieldConfigRegistry: plugin.fieldConfigRegistry,
    })[0];
  }, [state.items, options.columns, fieldConfig, theme, replaceVariables, tableName]);

  const changeWidth = (name: string, value: number) => {
    const overrides = fieldConfig.overrides.map((override) => ({ ...override, properties: [...override.properties] }));
    let override = overrides.find((item) => item.matcher.id === FieldMatcherID.byName && item.matcher.options === name);
    if (!override) {
      override = { matcher: { id: FieldMatcherID.byName, options: name }, properties: [] };
      overrides.push(override);
    }
    override.properties = override.properties.filter((property) => property.id !== 'custom.width');
    override.properties.push({ id: 'custom.width', value });
    props.onFieldConfigChange({ ...fieldConfig, overrides });
  };
  const footerHeight = 76;
  const errorHeight = state.error ? 90 : 0;
  return <div style={{ width, height, display: 'flex', flexDirection: 'column' }}>
    {state.error && <Alert title={state.error} severity="warning" />}
    <div style={{ flex: 1, minHeight: 0, opacity: state.loading ? 0.55 : 1 }} aria-busy={state.loading}>
      <Table key={pageSize} ariaLabel={tableName} data={frame} width={width}
        height={Math.max(80, height - footerHeight - errorHeight)} enablePagination={false}
        resizable onColumnResize={changeWidth}
        fieldConfig={fieldConfig} replaceVariables={replaceVariables} timeRange={props.timeRange} />
    </div>
    <div style={{ padding: '8px', display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '8px' }}>
      <Button variant="secondary" size="sm" disabled={state.loading || state.pageIndex === 0}
        onClick={() => { void pager.previous(); }}>Previous</Button>
      <span aria-live="polite">Page {state.pageIndex + 1} · {state.items.length} rows{state.loading ? ' · Loading…' : ''}</span>
      <Button variant="secondary" size="sm" disabled={state.loading || !state.next_cursor}
        onClick={() => { void pager.next(); }}>Next</Button>
      <Select aria-label="Rows per page" width={14} value={pageSize} disabled={state.loading}
        options={[50, 100, 200].map((value) => ({ label: `${value} rows`, value }))}
        onChange={(choice) => setPageSize(choice.value!)} />
      <Button variant="secondary" size="sm" disabled={state.loading} onClick={() => { void pager.refresh(); }}>
        {state.error ? 'Retry' : 'Refresh page'}
      </Button>
      <span style={{ width: '100%', fontSize: '12px', color: theme.colors.text.secondary }}>
        Column sorting and filters apply to this page.
        {resource === 'evidence' && ' Outcome filters the entire run.'}
        {resource === 'data-quality' && ' Affected keys show the first 20; see the run detail for all evidence.'}
      </span>
    </div>
  </div>;
}

// Grafana can run under a URL subpath. Its runtime config supplies that prefix.
function getBackendSrvBase() { return config.appSubUrl || ''; }

export const plugin = new PanelPlugin<Options>(EvidencePanel)
  .setNoPadding()
  .useFieldConfig({
    useCustomConfig: (builder) => builder
      .addNumberInput({ path: 'width', name: 'Column width', settings: { min: 50 } })
      .addSelect({ path: 'align', name: 'Alignment', defaultValue: 'auto', settings: {
        options: ['auto', 'left', 'center', 'right'].map((value) => ({ label: value, value })),
      } })
      .addBooleanSwitch({ path: 'filterable', name: 'Column filter', defaultValue: true })
      .addSelect({ path: 'cellOptions', name: 'Cell display', defaultValue: { type: 'auto' }, settings: {
        options: [
          { label: 'Auto', value: { type: 'auto' } },
          { label: 'Colored background', value: { type: 'color-background' } },
          { label: 'Colored text', value: { type: 'color-text' } },
        ],
      } }),
  })
  .setPanelOptions((builder) => builder
    .addSelect({ path: 'resource', name: 'Table data', defaultValue: 'evidence', settings: { options: [
      { label: 'Transaction evidence', value: 'evidence' }, { label: 'Refund cross-checks', value: 'refunds' },
      { label: 'Daily Data Quality', value: 'data-quality' },
    ] } })
    .addTextInput({ path: 'datasourceUid', name: 'API datasource UID', defaultValue: 'reconciliation-api' })
    .addTextInput({ path: 'runId', name: 'Run ID', defaultValue: '${run_id}' })
    .addTextInput({ path: 'outcome', name: 'Outcome', defaultValue: '${result_type}' })
    .addSelect({ path: 'pageSize', name: 'Rows per page', defaultValue: 200, settings: {
      options: [50, 100, 200].map((value) => ({ label: String(value), value })),
    } }));
