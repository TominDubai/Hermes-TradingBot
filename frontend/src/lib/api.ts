const BASE = '/api';

export interface HealthResponse {
  status: string;
  version: string;
  env: string;
  halted: boolean;
  alpaca_configured: boolean;
  telegram_configured: boolean;
}

export interface StatusResponse {
  phase: string;
  message: string;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`API ${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

export const api = {
  health: () => get<HealthResponse>('/health'),
  status: () => get<StatusResponse>(`${BASE}/status`),
};
