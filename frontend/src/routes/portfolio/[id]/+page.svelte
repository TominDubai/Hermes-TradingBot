<script lang="ts">
  import { page } from '$app/stores';
  import { onMount } from 'svelte';
  import { api, type Portfolio, type Signal, type Position } from '$lib/api';
  import { ws } from '$lib/ws';

  let portfolioId = $derived($page.params.id as 'long' | 'mid' | 'intra');

  let portfolio = $state<Portfolio | null>(null);
  let signals = $state<Signal[]>([]);
  let positions = $state<Position[]>([]);
  let loading = $state(true);

  async function loadData() {
    loading = true;
    try {
      const [p, sRes, posRes] = await Promise.all([
        api.portfolio(portfolioId),
        api.signals({ portfolio: portfolioId, limit: 100 }),
        api.portfolioPositions(portfolioId),
      ]);
      portfolio = p;
      signals = sRes.signals;
      positions = posRes.positions;
    } catch {
      portfolio = null;
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    loadData();
    const refreshInterval = setInterval(loadData, 30000);

    const unsub = ws.on('signal_detected', (data) => {
      const s = data as Signal;
      if (s.portfolio === portfolioId) signals = [s, ...signals];
    });

    return () => {
      clearInterval(refreshInterval);
      unsub();
    };
  });

  $effect(() => {
    portfolioId;
    loadData();
  });

  function fmt(n: number | null | undefined, digits = 2) {
    if (n == null) return '—';
    return n.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }

  function fmtPnl(n: number) {
    return (n >= 0 ? '+' : '') + fmt(n);
  }

  function relTime(iso: string) {
    const diff = Date.now() - new Date(iso).getTime();
    const m = Math.floor(diff / 60000);
    if (m < 1) return 'just now';
    if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ago`;
    return `${Math.floor(h / 24)}d ago`;
  }

  function scoreBadgeClass(score: number | null) {
    if (score == null) return 'bg-gray-500/20 text-gray-400';
    if (score >= 3) return 'bg-yellow-500/20 text-yellow-400';
    if (score >= 2) return 'bg-blue-500/20 text-blue-400';
    return 'bg-gray-500/20 text-gray-400';
  }

  // Equity curve placeholder data
  const equityCurve = [100,101,99,103,105,104,108,107,112,110,115,118,116,120];
  function buildPath(pts: number[]): string {
    const w = 400, h = 80;
    const min = Math.min(...pts), max = Math.max(...pts);
    const range = max - min || 1;
    const xs = pts.map((_, i) => (i / (pts.length - 1)) * w);
    const ys = pts.map((v) => h - ((v - min) / range) * (h - 4));
    const line = xs.map((x, i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${ys[i].toFixed(1)}`).join(' ');
    const area = `${line} L${w},${h} L0,${h} Z`;
    return JSON.stringify({ line, area });
  }
  const paths = JSON.parse(buildPath(equityCurve));

  const portfolioMeta: Record<string, { label: string; icon: string }> = {
    long: { label: 'Long Portfolio', icon: '📈' },
    mid: { label: 'Mid Portfolio', icon: '📊' },
    intra: { label: 'Intraday Portfolio', icon: '⏱' },
  };
</script>

<div class="max-w-6xl mx-auto space-y-6">
  <!-- Header -->
  <div class="flex items-center gap-3">
    <a href="/" class="text-sm hover:underline" style="color: var(--color-muted);">← Home</a>
    <span style="color: var(--color-border);">/</span>
    <span class="text-xl font-bold">
      {portfolioMeta[portfolioId]?.icon ?? ''} {portfolio?.name ?? portfolioMeta[portfolioId]?.label ?? portfolioId}
    </span>
  </div>

  {#if portfolio?.description}
    <p class="text-sm" style="color: var(--color-muted);">{portfolio.description}</p>
  {/if}

  <!-- Stats row -->
  <div class="grid grid-cols-2 sm:grid-cols-4 gap-4">
    {#each [
      { label: 'Open Positions', value: portfolio?.open_positions ?? '—', color: 'var(--color-text)' },
      { label: 'Today P&L', value: portfolio ? fmtPnl(portfolio.today_pnl) : '—', color: (portfolio?.today_pnl ?? 0) >= 0 ? '#22c55e' : '#ef4444' },
      { label: '30d Win Rate', value: portfolio ? fmt(portfolio.win_rate_30d * 100, 1) + '%' : '—', color: 'var(--color-text)' },
      { label: 'Total Signals', value: signals.length, color: '#3b82f6' },
    ] as stat}
      <div class="rounded-xl p-4 border" style="background: var(--color-surface); border-color: var(--color-border);">
        <p class="text-xs uppercase tracking-wider mb-1" style="color: var(--color-muted);">{stat.label}</p>
        {#if loading}
          <div class="h-7 w-20 rounded animate-pulse" style="background: var(--color-border);"></div>
        {:else}
          <p class="text-2xl font-bold font-mono tabular-nums" style="color: {stat.color};">{stat.value}</p>
        {/if}
      </div>
    {/each}
  </div>

  <!-- Open positions ---->
  <div>
    <h2 class="text-sm font-semibold uppercase tracking-wider mb-3" style="color: var(--color-muted);">
      Open Positions {#if positions.length > 0}<span class="text-blue-400">({positions.length})</span>{/if}
    </h2>
    {#if loading}
      <div class="rounded-xl p-8 border text-center animate-pulse" style="background: var(--color-surface); border-color: var(--color-border);">
        <div class="h-4 w-48 rounded mx-auto" style="background: var(--color-border);"></div>
      </div>
    {:else if positions.length === 0}
      <div class="rounded-xl p-8 border text-center" style="background: var(--color-surface); border-color: var(--color-border);">
        <p style="color: var(--color-muted);">No open positions. Orders will execute when market is open.</p>
      </div>
    {:else}
      <div class="rounded-xl border overflow-x-auto" style="background: var(--color-surface); border-color: var(--color-border);">
        <table class="w-full text-sm">
          <thead>
            <tr class="border-b text-xs uppercase tracking-wider" style="border-color: var(--color-border); color: var(--color-muted);">
              <th class="text-left px-4 py-3 font-medium">Symbol</th>
              <th class="text-left px-4 py-3 font-medium">Market</th>
              <th class="text-right px-4 py-3 font-medium">Qty</th>
              <th class="text-right px-4 py-3 font-medium">Entry</th>
              <th class="text-right px-4 py-3 font-medium">Current</th>
              <th class="text-right px-4 py-3 font-medium">P&L</th>
              <th class="text-right px-4 py-3 font-medium">P&L %</th>
              <th class="text-left px-4 py-3 font-medium">Broker</th>
            </tr>
          </thead>
          <tbody>
            {#each positions as pos}
              {@const pnlPos = pos.unrealised_pnl >= 0}
              <tr class="border-b hover:bg-white/[0.02] transition-colors"
                style="border-color: var(--color-border);">
                <td class="px-4 py-3 font-bold font-mono">{pos.symbol}</td>
                <td class="px-4 py-3">
                  <span class="text-xs px-2 py-0.5 rounded font-semibold"
                    style="background: rgba(59,130,246,0.12); color: #3b82f6;">
                    {pos.market}
                  </span>
                </td>
                <td class="px-4 py-3 text-right font-mono tabular-nums">{pos.qty}</td>
                <td class="px-4 py-3 text-right font-mono tabular-nums">${fmt(pos.avg_entry)}</td>
                <td class="px-4 py-3 text-right font-mono tabular-nums">${fmt(pos.current_price)}</td>
                <td class="px-4 py-3 text-right font-mono tabular-nums {pnlPos ? 'text-green-400' : 'text-red-400'}">
                  {pnlPos ? '+' : ''}{fmt(pos.unrealised_pnl)}
                </td>
                <td class="px-4 py-3 text-right font-mono tabular-nums {pnlPos ? 'text-green-400' : 'text-red-400'}">
                  {pnlPos ? '+' : ''}{fmt(pos.unrealised_pnl_pct, 2)}%
                </td>
                <td class="px-4 py-3 text-xs" style="color: var(--color-muted);">{pos.broker}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  </div>

  <!-- Equity curve placeholder -->
  <div class="rounded-xl p-5 border" style="background: var(--color-surface); border-color: var(--color-border);">
    <p class="text-sm font-semibold mb-4">Equity Curve</p>
    <div class="relative">
      <svg width="100%" viewBox="0 0 400 80" preserveAspectRatio="none" class="w-full h-20">
        <defs>
          <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#22c55e" stop-opacity="0.25"/>
            <stop offset="100%" stop-color="#22c55e" stop-opacity="0"/>
          </linearGradient>
        </defs>
        <path d={paths.area} fill="url(#equityGrad)" />
        <path d={paths.line} stroke="#22c55e" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
      <p class="text-xs text-center mt-2" style="color: var(--color-muted);">Placeholder — live chart coming in Phase 4</p>
    </div>
  </div>

  <!-- Signals table -->
  <div>
    <h2 class="text-sm font-semibold uppercase tracking-wider mb-3" style="color: var(--color-muted);">Signals</h2>
    <div class="rounded-xl border overflow-x-auto" style="background: var(--color-surface); border-color: var(--color-border);">
      {#if loading}
        <div class="p-8 text-center animate-pulse" style="color: var(--color-muted);">Loading…</div>
      {:else if signals.length === 0}
        <div class="p-12 text-center">
          <p class="text-4xl mb-3">📭</p>
          <p class="font-medium">No signals for this portfolio</p>
        </div>
      {:else}
        <table class="w-full text-sm">
          <thead>
            <tr class="border-b text-xs uppercase tracking-wider" style="border-color: var(--color-border); color: var(--color-muted);">
              <th class="text-left px-4 py-3 font-medium">Symbol</th>
              <th class="text-left px-4 py-3 font-medium">Setup</th>
              <th class="text-left px-4 py-3 font-medium">Dir.</th>
              <th class="text-center px-4 py-3 font-medium">Score</th>
              <th class="text-right px-4 py-3 font-medium">Entry</th>
              <th class="text-right px-4 py-3 font-medium">Stop</th>
              <th class="text-right px-4 py-3 font-medium">Target</th>
              <th class="text-right px-4 py-3 font-medium">R:R</th>
              <th class="text-right px-4 py-3 font-medium">Time</th>
              <th class="text-center px-4 py-3 font-medium">Outcome</th>
            </tr>
          </thead>
          <tbody>
            {#each signals as sig}
              {@const isHigh = (sig.confluence_score ?? 0) >= 3}
              <tr class="border-b {isHigh ? 'high-signal-row' : ''} hover:bg-white/[0.02] transition-colors"
                style="border-color: var(--color-border);">
                <td class="px-4 py-3 font-bold font-mono">{sig.symbol}</td>
                <td class="px-4 py-3 max-w-[140px] truncate" style="color: var(--color-muted);">{sig.setup_name}</td>
                <td class="px-4 py-3">
                  <span class="{sig.direction === 'long' ? 'text-green-400' : 'text-red-400'} font-semibold">
                    {sig.direction === 'long' ? '↑' : '↓'} {sig.direction}
                  </span>
                </td>
                <td class="px-4 py-3 text-center">
                  <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold {scoreBadgeClass(sig.confluence_score)}">
                    {sig.confluence_score ?? '—'}
                  </span>
                </td>
                <td class="px-4 py-3 text-right font-mono tabular-nums">${fmt(sig.entry_price)}</td>
                <td class="px-4 py-3 text-right font-mono tabular-nums text-red-400">${fmt(sig.stop_price)}</td>
                <td class="px-4 py-3 text-right font-mono tabular-nums text-green-400">${fmt(sig.target_price)}</td>
                <td class="px-4 py-3 text-right font-mono tabular-nums">{fmt(sig.rr_ratio, 1)}x</td>
                <td class="px-4 py-3 text-right font-mono tabular-nums text-xs" style="color: var(--color-muted);">{relTime(sig.detected_at)}</td>
                <td class="px-4 py-3 text-center text-xs font-semibold">
                  {#if sig.outcome === 'WIN'}
                    <span class="text-green-400">WIN</span>
                  {:else if sig.outcome === 'LOSS'}
                    <span class="text-red-400">LOSS</span>
                  {:else if sig.outcome === 'EXPIRED'}
                    <span class="text-yellow-600">EXPIRED</span>
                  {:else}
                    <span style="color: var(--color-muted);">open</span>
                  {/if}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    </div>
  </div>
</div>
