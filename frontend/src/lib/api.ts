const BASE = '/api';

// ─── Types ────────────────────────────────────────────────────────────────────

export interface HealthResponse {
  status: string;
  version: string;
  env: string;
  halted: boolean;
  alpaca_configured: boolean;
  telegram_configured: boolean;
}

export interface Signal {
  id: string;
  symbol: string;
  portfolio: 'long' | 'mid' | 'intra';
  direction: 'long' | 'short';
  setup_name: string;
  timeframe: string;
  confluence_score: number | null;
  entry_price: number;
  stop_price: number;
  target_price: number;
  rr_ratio: number;
  detected_at: string;
  outcome: null | 'WIN' | 'LOSS' | 'EXPIRED';
}

export interface SignalsResponse {
  total: number;
  signals: Signal[];
}

export interface Portfolio {
  id: string;
  name: string;
  description: string;
  open_positions: number;
  today_pnl: number;
  win_rate_30d: number;
  equity: number;
}

export interface PortfoliosResponse {
  portfolios: Portfolio[];
}

export interface Position {
  symbol: string;
  qty: number;
  avg_entry: number;
  current_price: number;
  unrealised_pnl: number;
  unrealised_pnl_pct: number;
  side: string;
  broker: string;
  market: string;
}

export interface PositionsResponse {
  portfolio: string;
  count: number;
  positions: Position[];
}

export interface Settings {
  halted: boolean;
  min_confluence: number;
  max_positions_long?: number;
  max_positions_mid?: number;
  max_positions_intra?: number;
  daily_loss_limit_pct: number;
}

export interface PerformanceSummary {
  open_positions: number;
  message: string;
}

export interface BacktestResult {
  status: string;
  [key: string]: unknown;
}

// ─── HTTP helpers ─────────────────────────────────────────────────────────────

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`API ${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method: 'POST',
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`API POST ${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

async function patch<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`API PATCH ${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

// ─── API surface ──────────────────────────────────────────────────────────────

export const api = {
  health: () => get<HealthResponse>('/health'),

  signals: (params?: { portfolio?: string; min_score?: number; limit?: number }) => {
    const q = new URLSearchParams();
    if (params?.portfolio) q.set('portfolio', params.portfolio);
    if (params?.min_score != null) q.set('min_score', String(params.min_score));
    if (params?.limit != null) q.set('limit', String(params.limit));
    const qs = q.toString();
    return get<SignalsResponse>(`${BASE}/signals${qs ? '?' + qs : ''}`);
  },

  signal: (id: string) => get<Signal>(`${BASE}/signals/${id}`),

  portfolios: () => get<PortfoliosResponse>(`${BASE}/portfolios`),
  portfolio: (id: string) => get<Portfolio>(`${BASE}/portfolios/${id}`),
  portfolioPositions: (id: string) => get<PositionsResponse>(`${BASE}/portfolios/${id}/positions`),

  settings: () => get<Settings>(`${BASE}/settings`),
  updateSettings: (s: Partial<Settings>) => patch<Settings>(`${BASE}/settings`, s),
  halt: () => post<{ ok: boolean }>(`${BASE}/settings/halt`),
  resume: () => post<{ ok: boolean }>(`${BASE}/settings/resume`),

  performance: () => get<PerformanceSummary>(`${BASE}/performance/summary`),

  backtest: (params: { portfolio: string; setup_name: string; symbols: string }) =>
    post<BacktestResult>(
      `${BASE}/backtest/run?portfolio=${params.portfolio}&setup_name=${params.setup_name}&symbols=${params.symbols}`
    ),
};
