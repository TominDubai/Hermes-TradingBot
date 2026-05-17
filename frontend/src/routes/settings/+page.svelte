<script lang="ts">
  import { onMount } from 'svelte';
  import { api, type Settings, type HealthResponse } from '$lib/api';

  let settings = $state<Settings | null>(null);
  let health = $state<HealthResponse | null>(null);
  let loading = $state(true);
  let saving = $state(false);
  let halting = $state(false);
  let saveMsg = $state('');
  let errorMsg = $state('');

  // Form values
  let minConfluence = $state(2);
  let dailyLossLimitPct = $state(2.0);
  let maxPositionsLong = $state(5);
  let maxPositionsMid = $state(5);
  let maxPositionsIntra = $state(10);

  async function loadData() {
    try {
      [settings, health] = await Promise.all([api.settings(), api.health()]);
      if (settings) {
        minConfluence = settings.min_confluence;
        dailyLossLimitPct = settings.daily_loss_limit_pct;
        maxPositionsLong = settings.max_positions_long ?? 5;
        maxPositionsMid = settings.max_positions_mid ?? 5;
        maxPositionsIntra = settings.max_positions_intra ?? 10;
      }
    } catch {
      errorMsg = 'Failed to load settings — backend may be offline.';
    } finally {
      loading = false;
    }
  }

  async function saveSettings() {
    saving = true;
    saveMsg = '';
    errorMsg = '';
    try {
      settings = await api.updateSettings({
        min_confluence: minConfluence,
        daily_loss_limit_pct: dailyLossLimitPct,
        max_positions_long: maxPositionsLong,
        max_positions_mid: maxPositionsMid,
        max_positions_intra: maxPositionsIntra,
      });
      saveMsg = '✓ Settings saved';
    } catch (e) {
      errorMsg = e instanceof Error ? e.message : 'Save failed';
    } finally {
      saving = false;
    }
  }

  async function toggleHalt() {
    halting = true;
    saveMsg = '';
    errorMsg = '';
    try {
      if (settings?.halted) {
        await api.resume();
      } else {
        await api.halt();
      }
      await loadData();
      saveMsg = settings?.halted ? 'Trading halted.' : 'Trading resumed.';
    } catch (e) {
      errorMsg = e instanceof Error ? e.message : 'Action failed';
    } finally {
      halting = false;
    }
  }

  onMount(loadData);
</script>

<div class="max-w-3xl mx-auto space-y-6">
  <h1 class="text-xl font-bold">⚙ Settings</h1>

  {#if errorMsg}
    <div class="px-4 py-3 rounded-lg text-sm"
      style="background: rgba(239,68,68,0.1); border: 1px solid rgba(239,68,68,0.3); color: #ef4444;">
      {errorMsg}
    </div>
  {/if}

  <!-- ─── Kill switch ────────────────────────────────────────── -->
  <div class="rounded-xl p-6 border" style="background: var(--color-surface); border-color: var(--color-border);">
    <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
      <div>
        <h2 class="font-semibold text-lg">Kill Switch</h2>
        {#if loading}
          <div class="h-4 w-32 rounded animate-pulse mt-1" style="background: var(--color-border);"></div>
        {:else}
          <p class="text-sm mt-1 {settings?.halted ? 'text-red-400' : 'text-green-400'}">
            {settings?.halted ? '🔴 Trading is HALTED' : '✅ Trading is ACTIVE'}
          </p>
        {/if}
      </div>
      <button
        onclick={toggleHalt}
        disabled={halting || loading}
        class="px-6 py-3 rounded-lg font-bold text-sm transition-colors disabled:opacity-50 min-w-[140px]"
        style={settings?.halted
          ? 'background: #22c55e; color: #000;'
          : 'background: #ef4444; color: #fff;'}>
        {halting ? '⏳ …' : settings?.halted ? '▶ Resume Trading' : '⏹ Halt Trading'}
      </button>
    </div>
  </div>

  <!-- ─── Trading settings form ────────────────────────────── -->
  <div class="rounded-xl p-6 border space-y-5" style="background: var(--color-surface); border-color: var(--color-border);">
    <h2 class="font-semibold">Trading Parameters</h2>

    <div class="grid grid-cols-1 sm:grid-cols-2 gap-5">
      <div>
        <label for="min-confluence" class="block text-xs mb-1.5" style="color: var(--color-muted);">
          Min Confluence Score (1–6)
        </label>
        <input id="min-confluence" type="number" bind:value={minConfluence} min="1" max="6" step="1"
          class="w-full px-3 py-2 rounded-lg text-sm border font-mono tabular-nums focus:outline-none focus:ring-2 focus:ring-blue-500/50"
          style="background: var(--color-bg); border-color: var(--color-border); color: var(--color-text);" />
      </div>
      <div>
        <label for="daily-loss" class="block text-xs mb-1.5" style="color: var(--color-muted);">
          Daily Loss Limit (%)
        </label>
        <input id="daily-loss" type="number" bind:value={dailyLossLimitPct} min="0" max="100" step="0.1"
          class="w-full px-3 py-2 rounded-lg text-sm border font-mono tabular-nums focus:outline-none focus:ring-2 focus:ring-blue-500/50"
          style="background: var(--color-bg); border-color: var(--color-border); color: var(--color-text);" />
      </div>
    </div>

    <div class="grid grid-cols-1 sm:grid-cols-3 gap-5">
      <div>
        <label for="max-long" class="block text-xs mb-1.5" style="color: var(--color-muted);">Max Positions — Long</label>
        <input id="max-long" type="number" bind:value={maxPositionsLong} min="1" max="50" step="1"
          class="w-full px-3 py-2 rounded-lg text-sm border font-mono tabular-nums focus:outline-none focus:ring-2 focus:ring-blue-500/50"
          style="background: var(--color-bg); border-color: var(--color-border); color: var(--color-text);" />
      </div>
      <div>
        <label for="max-mid" class="block text-xs mb-1.5" style="color: var(--color-muted);">Max Positions — Mid</label>
        <input id="max-mid" type="number" bind:value={maxPositionsMid} min="1" max="50" step="1"
          class="w-full px-3 py-2 rounded-lg text-sm border font-mono tabular-nums focus:outline-none focus:ring-2 focus:ring-blue-500/50"
          style="background: var(--color-bg); border-color: var(--color-border); color: var(--color-text);" />
      </div>
      <div>
        <label for="max-intra" class="block text-xs mb-1.5" style="color: var(--color-muted);">Max Positions — Intra</label>
        <input id="max-intra" type="number" bind:value={maxPositionsIntra} min="1" max="100" step="1"
          class="w-full px-3 py-2 rounded-lg text-sm border font-mono tabular-nums focus:outline-none focus:ring-2 focus:ring-blue-500/50"
          style="background: var(--color-bg); border-color: var(--color-border); color: var(--color-text);" />
      </div>
    </div>

    {#if saveMsg}
      <p class="text-sm text-green-400">{saveMsg}</p>
    {/if}

    <button onclick={saveSettings} disabled={saving || loading}
      class="px-5 py-2 rounded-lg text-sm font-semibold transition-colors disabled:opacity-50"
      style="background: #3b82f6; color: #fff;">
      {saving ? '⏳ Saving…' : '💾 Save Settings'}
    </button>
  </div>

  <!-- ─── Backend info card ────────────────────────────────── -->
  <div class="rounded-xl p-5 border" style="background: var(--color-surface); border-color: var(--color-border);">
    <h2 class="font-semibold mb-4">Backend Info</h2>
    {#if loading}
      <div class="space-y-2">
        {#each [1,2,3,4] as _}
          <div class="h-5 rounded animate-pulse" style="background: var(--color-border);"></div>
        {/each}
      </div>
    {:else if health}
      <div class="space-y-3 text-sm">
        <div class="flex justify-between border-b pb-2" style="border-color: var(--color-border);">
          <span style="color: var(--color-muted);">Status</span>
          <span class="{health.status === 'ok' ? 'text-green-400' : 'text-red-400'} font-semibold">
            {health.status === 'ok' ? '● Online' : '● Error'}
          </span>
        </div>
        <div class="flex justify-between border-b pb-2" style="border-color: var(--color-border);">
          <span style="color: var(--color-muted);">Version</span>
          <span class="font-mono tabular-nums">v{health.version}</span>
        </div>
        <div class="flex justify-between border-b pb-2" style="border-color: var(--color-border);">
          <span style="color: var(--color-muted);">Environment</span>
          <span class="font-mono uppercase text-xs px-2 py-0.5 rounded"
            style="background: rgba(255,255,255,0.06);">{health.env}</span>
        </div>
        <div class="flex justify-between border-b pb-2" style="border-color: var(--color-border);">
          <span style="color: var(--color-muted);">Alpaca</span>
          <span class="{health.alpaca_configured ? 'text-green-400' : 'text-yellow-400'}">
            {health.alpaca_configured ? '✅ Configured' : '⚠ Not configured'}
          </span>
        </div>
        <div class="flex justify-between">
          <span style="color: var(--color-muted);">Telegram</span>
          <span class="{health.telegram_configured ? 'text-green-400' : 'text-yellow-400'}">
            {health.telegram_configured ? '✅ Configured' : '⚠ Not configured'}
          </span>
        </div>
      </div>
    {:else}
      <p class="text-sm" style="color: var(--color-muted);">Backend offline</p>
    {/if}
  </div>
</div>
