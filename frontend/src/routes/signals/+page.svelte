<script lang="ts">
  import { onMount } from 'svelte';
  import { api, type Signal } from '$lib/api';
  import { ws } from '$lib/ws';

  let signals = $state<Signal[]>([]);
  let loading = $state(true);
  let portfolioFilter = $state<string>('all');
  let scoreFilter = $state<string>('all');

  async function loadSignals() {
    try {
      const params: { portfolio?: string; min_score?: number; limit?: number } = { limit: 200 };
      if (portfolioFilter !== 'all') params.portfolio = portfolioFilter;
      if (scoreFilter === 'medium') params.min_score = 2;
      if (scoreFilter === 'high') params.min_score = 3;
      const res = await api.signals(params);
      signals = res.signals;
    } catch {
      signals = [];
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    loadSignals();

    const unsub = ws.on('signal_detected', (data) => {
      const s = data as Signal;
      signals = [s, ...signals];
    });

    const unsub2 = ws.on('signal_scored', (data) => {
      const s = data as Signal;
      signals = signals.map((x) => (x.id === s.id ? s : x));
    });

    return () => { unsub(); unsub2(); };
  });

  $effect(() => {
    // re-fetch when filters change
    portfolioFilter;
    scoreFilter;
    loadSignals();
  });

  function fmt(n: number | null | undefined, digits = 2) {
    if (n == null) return '—';
    return n.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
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

  function outcomeClass(outcome: string | null) {
    if (!outcome) return 'text-gray-500';
    if (outcome === 'WIN') return 'text-green-400';
    if (outcome === 'LOSS') return 'text-red-400';
    return 'text-yellow-600';
  }
</script>

<div class="max-w-7xl mx-auto space-y-5">
  <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
    <h1 class="text-xl font-bold">⚡ Live Signals</h1>
    <span class="text-sm font-mono tabular-nums" style="color: var(--color-muted);">
      {signals.length} signals
    </span>
  </div>

  <!-- ─── Filters ───────────────────────────────────────────── -->
  <div class="flex flex-wrap gap-2">
    <!-- Portfolio filter -->
    <div class="flex gap-1">
      {#each ['all', 'long', 'mid', 'intra'] as opt}
        <button
          class="px-3 py-1.5 rounded-lg text-xs font-medium transition-colors"
          style={portfolioFilter === opt
            ? 'background: rgba(59,130,246,0.2); color: #3b82f6; border: 1px solid rgba(59,130,246,0.4);'
            : 'background: var(--color-surface); color: var(--color-muted); border: 1px solid var(--color-border);'}
          onclick={() => (portfolioFilter = opt)}>
          {opt === 'all' ? 'All Portfolios' : opt.charAt(0).toUpperCase() + opt.slice(1)}
        </button>
      {/each}
    </div>

    <!-- Score filter -->
    <div class="flex gap-1 ml-auto">
      {#each [['all', 'All Scores'], ['medium', 'Medium+ (≥2)'], ['high', 'High (≥3)']] as [val, label]}
        <button
          class="px-3 py-1.5 rounded-lg text-xs font-medium transition-colors"
          style={scoreFilter === val
            ? 'background: rgba(227,179,65,0.15); color: #e3b341; border: 1px solid rgba(227,179,65,0.4);'
            : 'background: var(--color-surface); color: var(--color-muted); border: 1px solid var(--color-border);'}
          onclick={() => (scoreFilter = val)}>
          {label}
        </button>
      {/each}
    </div>
  </div>

  <!-- ─── Table ──────────────────────────────────────────────── -->
  <div class="rounded-xl border overflow-x-auto"
    style="background: var(--color-surface); border-color: var(--color-border);">

    {#if loading}
      <div class="p-8 text-center animate-pulse" style="color: var(--color-muted);">Loading signals…</div>
    {:else if signals.length === 0}
      <div class="p-12 text-center">
        <p class="text-4xl mb-3">📭</p>
        <p class="font-medium">No signals found</p>
        <p class="text-sm mt-1" style="color: var(--color-muted);">Signals will appear here when detected.</p>
      </div>
    {:else}
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b text-xs uppercase tracking-wider" style="border-color: var(--color-border); color: var(--color-muted);">
            <th class="text-left px-4 py-3 font-medium">Symbol</th>
            <th class="text-left px-4 py-3 font-medium">Setup</th>
            <th class="text-left px-4 py-3 font-medium">Port.</th>
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
                <span class="px-2 py-0.5 rounded text-xs uppercase font-semibold"
                  style="background: rgba(255,255,255,0.06); color: var(--color-muted);">
                  {sig.portfolio}
                </span>
              </td>
              <td class="px-4 py-3">
                <span class="{sig.direction === 'long' ? 'text-green-400' : 'text-red-400'} font-semibold flex items-center gap-1">
                  {sig.direction === 'long' ? '↑' : '↓'}
                  <span class="text-xs capitalize">{sig.direction}</span>
                </span>
              </td>
              <td class="px-4 py-3 text-center">
                <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold {scoreBadgeClass(sig.confluence_score)} {isHigh ? 'badge-gold-pulse' : ''}">
                  {sig.confluence_score ?? '—'}
                </span>
              </td>
              <td class="px-4 py-3 text-right font-mono tabular-nums">${fmt(sig.entry_price)}</td>
              <td class="px-4 py-3 text-right font-mono tabular-nums text-red-400">${fmt(sig.stop_price)}</td>
              <td class="px-4 py-3 text-right font-mono tabular-nums text-green-400">${fmt(sig.target_price)}</td>
              <td class="px-4 py-3 text-right font-mono tabular-nums">{fmt(sig.rr_ratio, 1)}x</td>
              <td class="px-4 py-3 text-right font-mono tabular-nums text-xs" style="color: var(--color-muted);">
                {relTime(sig.detected_at)}
              </td>
              <td class="px-4 py-3 text-center">
                {#if sig.outcome}
                  <span class="text-xs font-semibold {outcomeClass(sig.outcome)}">{sig.outcome}</span>
                {:else}
                  <span class="text-xs" style="color: var(--color-muted);">open</span>
                {/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </div>
</div>
