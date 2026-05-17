<script lang="ts">
  import { onMount } from 'svelte';
  import { api, type PerformanceSummary, type BacktestResult } from '$lib/api';

  let perfSummary = $state<PerformanceSummary | null>(null);
  let loading = $state(true);

  // Backtest form
  let btPortfolio = $state('mid');
  let btSetup = $state('mean_reversion');
  let btSymbols = $state('AAPL,MSFT,GOOGL');
  let btRunning = $state(false);
  let btResult = $state<BacktestResult | null>(null);
  let btError = $state('');

  async function loadPerf() {
    try {
      perfSummary = await api.performance();
    } catch {
      perfSummary = null;
    } finally {
      loading = false;
    }
  }

  async function runBacktest() {
    btRunning = true;
    btError = '';
    btResult = null;
    try {
      btResult = await api.backtest({
        portfolio: btPortfolio,
        setup_name: btSetup,
        symbols: btSymbols,
      });
    } catch (e) {
      btError = e instanceof Error ? e.message : 'Backtest failed';
    } finally {
      btRunning = false;
    }
  }

  onMount(loadPerf);
</script>

<div class="max-w-4xl mx-auto space-y-6">
  <div>
    <h1 class="text-xl font-bold">🔬 Analytics</h1>
    <p class="text-sm mt-1" style="color: var(--color-muted);">Performance insights and backtesting</p>
  </div>

  <!-- Performance summary -->
  <div class="rounded-xl p-5 border" style="background: var(--color-surface); border-color: var(--color-border);">
    <h2 class="text-sm font-semibold uppercase tracking-wider mb-4" style="color: var(--color-muted);">Performance Summary</h2>
    {#if loading}
      <div class="animate-pulse" style="color: var(--color-muted);">Loading…</div>
    {:else if perfSummary}
      <div class="flex items-center gap-6">
        <div>
          <p class="text-xs" style="color: var(--color-muted);">Open Positions</p>
          <p class="text-3xl font-bold font-mono tabular-nums mt-1">{perfSummary.open_positions}</p>
        </div>
        <div class="flex-1">
          <p class="text-xs" style="color: var(--color-muted);">Message</p>
          <p class="text-sm mt-1">{perfSummary.message}</p>
        </div>
      </div>
    {:else}
      <p class="text-sm" style="color: var(--color-muted);">Performance data unavailable — backend may be offline.</p>
    {/if}
  </div>

  <!-- Coming soon tile -->
  <div class="rounded-xl p-8 border text-center"
    style="background: var(--color-surface); border-color: var(--color-border);">
    <p class="text-5xl mb-4">📈</p>
    <h3 class="text-lg font-semibold mb-2">Deep Analytics — Coming Soon</h3>
    <p class="text-sm max-w-sm mx-auto" style="color: var(--color-muted);">
      Equity curves, win/loss heatmaps, setup performance breakdown, drawdown analysis, and Sharpe ratio — shipping in Phase 4.
    </p>
  </div>

  <!-- Backtest form -->
  <div class="rounded-xl p-5 border" style="background: var(--color-surface); border-color: var(--color-border);">
    <h2 class="text-sm font-semibold uppercase tracking-wider mb-4" style="color: var(--color-muted);">Run Backtest</h2>
    <div class="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-4">
      <div>
        <label for="bt-portfolio" class="block text-xs mb-1.5" style="color: var(--color-muted);">Portfolio</label>
        <select id="bt-portfolio" bind:value={btPortfolio}
          class="w-full px-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2 focus:ring-blue-500/50"
          style="background: var(--color-bg); border-color: var(--color-border); color: var(--color-text);">
          <option value="long">Long</option>
          <option value="mid">Mid</option>
          <option value="intra">Intra</option>
        </select>
      </div>
      <div>
        <label for="bt-setup" class="block text-xs mb-1.5" style="color: var(--color-muted);">Setup Name</label>
        <input id="bt-setup" type="text" bind:value={btSetup}
          placeholder="e.g. mean_reversion"
          class="w-full px-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2 focus:ring-blue-500/50"
          style="background: var(--color-bg); border-color: var(--color-border); color: var(--color-text);" />
      </div>
      <div>
        <label for="bt-symbols" class="block text-xs mb-1.5" style="color: var(--color-muted);">Symbols (comma-sep)</label>
        <input id="bt-symbols" type="text" bind:value={btSymbols}
          placeholder="AAPL,MSFT"
          class="w-full px-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2 focus:ring-blue-500/50"
          style="background: var(--color-bg); border-color: var(--color-border); color: var(--color-text);" />
      </div>
    </div>

    <button onclick={runBacktest} disabled={btRunning}
      class="px-5 py-2 rounded-lg text-sm font-semibold transition-colors disabled:opacity-50"
      style="background: #3b82f6; color: #fff;">
      {btRunning ? '⏳ Running…' : '▶ Run Backtest'}
    </button>

    {#if btError}
      <div class="mt-4 px-4 py-3 rounded-lg text-sm"
        style="background: rgba(239,68,68,0.1); border: 1px solid rgba(239,68,68,0.3); color: #ef4444;">
        {btError}
      </div>
    {/if}

    {#if btResult}
      <div class="mt-4 rounded-lg p-4 border overflow-x-auto"
        style="background: var(--color-bg); border-color: var(--color-border);">
        <p class="text-xs font-semibold mb-2" style="color: var(--color-muted);">Backtest Result</p>
        <pre class="text-xs font-mono whitespace-pre-wrap" style="color: var(--color-text);">{JSON.stringify(btResult, null, 2)}</pre>
      </div>
    {/if}
  </div>
</div>
