<script lang="ts">
  import '../app.css';
  import { api, type HealthResponse } from '$lib/api';
  import { onMount } from 'svelte';

  let health: HealthResponse | null = $state(null);
  let error = $state('');

  onMount(async () => {
    try {
      health = await api.health();
    } catch (e) {
      error = 'Backend offline — start with: cd backend && uv run uvicorn hermes.main:app --reload --port 8090';
    }
  });

  function statusColor(val: boolean) {
    return val ? 'text-green-400' : 'text-yellow-400';
  }
</script>

<main class="min-h-screen flex flex-col items-center justify-center gap-8 p-8">
  <div class="text-center">
    <h1 class="text-5xl font-bold tracking-tight mb-2">⚡ Hermes</h1>
    <p class="text-slate-500 text-lg">Algorithmic Trading Bot — Phase 0</p>
  </div>

  {#if error}
    <div class="bg-red-950 border border-red-800 text-red-300 rounded-lg px-6 py-4 max-w-lg text-sm font-mono">
      {error}
    </div>
  {:else if health}
    <div class="bg-slate-900 border border-slate-700 rounded-xl p-6 w-full max-w-sm space-y-3">
      <div class="flex items-center gap-2">
        <span class="w-2.5 h-2.5 rounded-full {health.status === 'ok' ? 'bg-green-400' : 'bg-red-400'}"></span>
        <span class="font-semibold">Backend {health.status === 'ok' ? 'online' : 'error'}</span>
        <span class="ml-auto text-slate-500 text-sm font-mono">v{health.version}</span>
      </div>

      <div class="border-t border-slate-700 pt-3 space-y-2 text-sm">
        <div class="flex justify-between">
          <span class="text-slate-400">Environment</span>
          <span class="font-mono">{health.env}</span>
        </div>
        <div class="flex justify-between">
          <span class="text-slate-400">Halted</span>
          <span class="{health.halted ? 'text-red-400' : 'text-green-400'}">{health.halted ? '🔴 YES' : '✅ No'}</span>
        </div>
        <div class="flex justify-between">
          <span class="text-slate-400">Alpaca</span>
          <span class="{statusColor(health.alpaca_configured)}">{health.alpaca_configured ? '✅ Configured' : '⚠ Not configured'}</span>
        </div>
        <div class="flex justify-between">
          <span class="text-slate-400">Telegram</span>
          <span class="{statusColor(health.telegram_configured)}">{health.telegram_configured ? '✅ Configured' : '⚠ Not configured'}</span>
        </div>
      </div>
    </div>
  {:else}
    <div class="text-slate-500 animate-pulse">Connecting to backend...</div>
  {/if}

  <p class="text-slate-600 text-xs">
    API docs:
    <a href="http://localhost:8090/docs" target="_blank" class="text-blue-400 hover:underline">
      localhost:8090/docs
    </a>
  </p>
</main>
