export type EvidenceRow = Record<string, string | number | null | number[]>;
export interface EvidencePage {
  items: EvidenceRow[];
  next_cursor: string | null;
}
export type PageLoader = (cursor: string | null, signal: AbortSignal) => Promise<EvidencePage>;
export interface PagerState extends EvidencePage {
  // Retain cursors only, never previous pages of transaction data.
  cursors: Array<string | null>;
  pageIndex: number;
  loading: boolean;
  error: string | null;
}

export class CursorPager {
  private state: PagerState = {
    items: [], next_cursor: null, cursors: [null], pageIndex: 0, loading: false, error: null,
  };
  private listeners = new Set<() => void>();
  private request?: AbortController;
  constructor(private loader: PageLoader) {}

  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  private update(state: PagerState) {
    this.state = state;
    this.listeners.forEach((listener) => listener());
  }
  private async load(cursor: string | null, pageIndex: number) {
    if (this.state.loading) { return; }
    const request = new AbortController();
    this.request = request;
    this.update({ ...this.state, loading: true, error: null });
    try {
      const page = await this.loader(cursor, request.signal);
      if (request.signal.aborted) { return; }
      const cursors = this.state.cursors.slice(0, pageIndex + 1);
      cursors[pageIndex] = cursor;
      this.update({ ...page, cursors, pageIndex, loading: false, error: null });
    } catch (error) {
      if (request.signal.aborted) { return; }
      this.update({ ...this.state, loading: false,
        error: error instanceof Error ? error.message : 'Could not load evidence. Try again.' });
    }
  }
  refresh = () => this.load(this.state.cursors[this.state.pageIndex], this.state.pageIndex);
  next = () => this.state.next_cursor
    ? this.load(this.state.next_cursor, this.state.pageIndex + 1) : Promise.resolve();
  previous = () => this.state.pageIndex > 0
    ? this.load(this.state.cursors[this.state.pageIndex - 1], this.state.pageIndex - 1) : Promise.resolve();
  dispose = () => {
    this.request?.abort();
    // Effects may be mounted again in React StrictMode.
    this.state = { ...this.state, loading: false };
  };
}
