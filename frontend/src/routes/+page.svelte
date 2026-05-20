<script lang="ts">
  import { onMount } from 'svelte';
  import { api, type Portfolio, type Signal } from '$lib/api';
  import { ws } from '$lib/ws';

  let portfolios = $state<Portfolio[]>([]);
  let recentSignals = $state<Signal[]>([]);
  let loading = $state(true);

  // Derived totals
  let totalEquity = $derived(portfolios.reduce((s, p) => s + (p.equity ?? 0), 0));
  let totalPnl = $derived(portfolios.reduce((s, p) => s + (p.today_pnl ?? 0), 0));

  async function loadData() {
    try {
      const [pRes, sRes] = await Promise.all([
        api.portfolios(),
        api.signals({ limit: 5, min_score: 1 }),
      ]);
      portfolios = pRes.portfolios;
      recentSignals = sRes.signals.slice(0, 5);
    } catch {
      // backend may be offline
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    loadData();

    // Auto-refresh every 30 seconds
    const refreshInterval = setInterval(loadData, 30000);

    const unsub = ws.on('signal_detected', (data) => {
      const s = data as Signal;
      recentSignals = [s, ...recentSignals].slice(0, 5);
    });

    // Also refresh on backlog (reconnect)
    const unsubBacklog = ws.on('backlog', (data) => {
      const signals = data as Signal[];
      if (signals?.length) recentSignals = signals.slice(0, 5);
      loadData(); // refresh portfolio stats too
    });

    return () => {
      clearInterval(refreshInterval);
      unsub();
      unsubBacklog();
    };
  });

  function fmt(n: number | null | undefined, digits = 2) {
    if (n == null) return '—';
    return n.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }

  function fmtPnl(n: number) {
    const sign = n >= 0 ? '+' : '';
    return `${sign}${fmt(n)}`;
  }

  function scoreBadgeClass(score: number | null) {
    if (score == null) return 'bg-gray-500/20 text-gray-400';
    if (score >= 3) return 'bg-yellow-500/20 text-yellow-400';
    if (score >= 2) return 'bg-blue-500/20 text-blue-400';
    return 'bg-gray-500/20 text-gray-400';
  }

  function scoreLabel(score: number | null) {
    if (score == null) return '—';
    if (score >= 3) return `★ ${score}`;
    return `${score}`;
  }

  const sparkPoints = [40, 42, 38, 45, 43, 48, 50, 47, 52, 55, 53, 58, 60];
  function buildSparkPath(pts: number[]): string {
    const w = 120, h = 40;
    const min = Math.min(...pts), max = Math.max(...pts);
    const range = max - min || 1;
    const xs = pts.map((_, i) => (i / (pts.length - 1)) * w);
    const ys = pts.map((v) => h - ((v - min) / range) * h);
    return xs.map((x, i) => `${i === 0 ? 'M' : 'L'}${x},${ys[i]}`).join(' ');
  }

  const portfolioMeta: Record<string, { icon: string; color: string }> = {
    long: { icon: '📈', color: '#22c55e' },
    mid: { icon: '📊', color: '#3b82f6' },
    intra: { icon: '⏱', color: '#e3b341' },
  };
</script>

<div class="space-y-6 max-w-6xl mx-auto">
  <!-- ─── Hero ──────────────────────────────────────────────── -->
  <div class="rounded-xl p-6 border"
    style="background: var(--color-surface); border-color: var(--color-border);">
    <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
      <div>
        <p class="text-sm" style="color: var(--color-muted);">Total Portfolio Equity</p>
        {#if loading}
          <div class="h-10 w-48 rounded animate-pulse mt-1" style="background: var(--color-border);"></div>
        {:else}
          <p class="text-4xl font-bold font-mono tabular-nums mt-1">${fmt(totalEquity)}</p>
          <p class="text-lg font-mono tabular-nums mt-1 {totalPnl >= 0 ? 'text-green-400' : 'text-red-400'}">
            {fmtPnl(totalPnl)} today
          </p>
        {/if}
      </div>

      <!-- Sparkline -->
      <div class="opacity-70">
        <svg width="120" height="40" viewBox="0 0 120 40" fill="none">
          <path d={buildSparkPath(sparkPoints)}
            stroke={totalPnl >= 0 ? '#22c55e' : '#ef4444'}
            stroke-width="2"
            fill="none"
            stroke-linecap="round"
            stroke-linejoin="round" />
        </svg>
        <p class="text-xs text-center mt-1" style="color: var(--color-muted);">30d trend</p>
      </div>
    </div>
  </div>

  <!-- ─── Portfolio cards ───────────────────────────────────── -->
  <section>
    <h2 class="text-sm font-semibold uppercase tracking-wider mb-3" style="color: var(--color-muted);">Portfolios</h2>
    <div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
      {#each ['long', 'mid', 'intra'] as pid}
        {@const p = portfolios.find((x) => x.id === pid)}
        {@const meta = portfolioMeta[pid]}
        <a href="/portfolio/{pid}"
          class="rounded-xl p-4 border block hover:border-blue-500/40 transition-colors"
          style="background: var(--color-surface); border-color: var(--color-border);">
          <div class="flex items-center justify-between mb-3">
            <div class="flex items-center gap-2">
              <span>{meta.icon}</span>
              <span class="font-semibold capitalize">{pid}</span>
            </div>
            {#if loading}
              <div class="h-4 w-16 rounded animate-pulse" style="background: var(--color-border);"></div>
            {:else}
              <span class="text-sm font-mono tabular-nums font-bold"
                style="color: {meta.color};">
                ${fmt(p?.equity ?? 0, 0)}
              </span>
            {/if}
          </div>
          {#if !loading}
            <div class="space-y-1.5 text-sm">
              <div class="flex justify-between">
                <span style="color: var(--color-muted);">Open positions</span>
                <span class="font-mono tabular-nums">{p?.open_positions ?? '—'}</span>
              </div>
              <div class="flex justify-between">
                <span style="color: var(--color-muted);">Today P&L</span>
                <span class="font-mono tabular-nums {(p?.today_pnl ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}">
                  {p ? fmtPnl(p.today_pnl) : '—'}
                </span>
              </div>
              <div class="flex justify-between">
                <span style="color: var(--color-muted);">30d win rate</span>
                <span class="font-mono tabular-nums">{p ? fmt(p.win_rate_30d * 100, 1) + '%' : '—'}</span>
              </div>
            </div>
          {:else}
            <div class="space-y-1.5">
              {#each [1,2,3] as _}
                <div class="h-4 rounded animate-pulse" style="background: var(--color-border);"></div>
              {/each}
            </div>
          {/if}
        </a>
      {/each}
    </div>
  </section>

  <!-- ─── Recent signals ────────────────────────────────────── -->
  <section>
    <div class="flex items-center justify-between mb-3">
      <h2 class="text-sm font-semibold uppercase tracking-wider" style="color: var(--color-muted);">Recent Signals</h2>
      <a href="/signals" class="text-xs text-blue-400 hover:underline">View all →</a>
    </div>

    {#if recentSignals.length === 0 && !loading}
      <div class="rounded-xl p-8 border text-center" style="background: var(--color-surface); border-color: var(--color-border);">
        <p style="color: var(--color-muted);">No signals yet. They'll appear here in real-time.</p>
      </div>
    {:else}
      <div class="space-y-2">
        {#each recentSignals as sig}
          {@const isHigh = (sig.confluence_score ?? 0) >= 3}
          <div class="rounded-xl px-4 py-3 border flex items-center gap-3 {isHigh ? 'high-signal-row' : ''}"
            style="background: var(--color-surface); border-color: var(--color-border);">
            <!-- Score badge -->
            <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold {scoreBadgeClass(sig.confluence_score)} {isHigh ? 'badge-gold-pulse' : ''}">
              {scoreLabel(sig.confluence_score)}
            </span>

            <!-- Symbol -->
            <span class="font-bold font-mono">{sig.symbol}</span>

            <!-- Direction -->
            <span class="{sig.direction === 'long' ? 'text-green-400' : 'text-red-400'} text-lg leading-none">
              {sig.direction === 'long' ? '↑' : '↓'}
            </span>

            <!-- Setup -->
            <span class="text-sm flex-1 truncate" style="color: var(--color-muted);">{sig.setup_name}</span>

            <!-- Portfolio pill -->
            <span class="text-xs px-2 py-0.5 rounded uppercase font-semibold"
              style="background: rgba(255,255,255,0.06); color: var(--color-muted);">
              {sig.portfolio}
            </span>

            <!-- Time -->
            <span class="text-xs font-mono tabular-nums" style="color: var(--color-muted);">
              {new Date(sig.detected_at).toLocaleTimeString()}
            </span>
          </div>
        {/each}
      </div>
    {/if}
  </section>
</div>
