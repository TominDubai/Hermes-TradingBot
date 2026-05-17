<script lang="ts">
  import '../app.css';
  import { page } from '$app/stores';
  import { onMount } from 'svelte';
  import { api, type HealthResponse, type Settings } from '$lib/api';
  import { ws } from '$lib/ws';

  let { children } = $props();

  let health = $state<HealthResponse | null>(null);
  let settings = $state<Settings | null>(null);
  let backendOnline = $state(false);

  async function pollHealth() {
    try {
      health = await api.health();
      backendOnline = health.status === 'ok';
    } catch {
      backendOnline = false;
      health = null;
    }
  }

  async function pollSettings() {
    try {
      settings = await api.settings();
    } catch {
      // not critical
    }
  }

  onMount(() => {
    pollHealth();
    pollSettings();
    ws.connect();

    const interval = setInterval(() => {
      pollHealth();
      pollSettings();
    }, 15000);

    return () => {
      clearInterval(interval);
      ws.disconnect();
    };
  });

  const navItems = [
    { href: '/', label: 'Home', icon: '◈' },
    { href: '/signals', label: 'Signals', icon: '⚡' },
    { href: '/portfolio/long', label: 'Long', icon: '📈' },
    { href: '/portfolio/mid', label: 'Mid', icon: '📊' },
    { href: '/portfolio/intra', label: 'Intra', icon: '⏱' },
    { href: '/analytics', label: 'Analytics', icon: '🔬' },
    { href: '/settings', label: 'Settings', icon: '⚙' },
  ];

  function isActive(href: string): boolean {
    if (href === '/') return $page.url.pathname === '/';
    return $page.url.pathname.startsWith(href);
  }
</script>

<div class="flex min-h-screen" style="background: var(--color-bg); color: var(--color-text);">
  <!-- ─── Sidebar (desktop) ─────────────────────────────────── -->
  <aside class="hidden md:flex flex-col w-56 shrink-0 border-r"
    style="background: var(--color-surface); border-color: var(--color-border);">

    <!-- Logo -->
    <div class="px-5 py-5 border-b" style="border-color: var(--color-border);">
      <div class="flex items-center gap-2">
        <span class="text-2xl">⚡</span>
        <span class="text-lg font-bold tracking-tight" style="color: var(--color-gold);">Hermes</span>
      </div>
      <p class="text-xs mt-0.5" style="color: var(--color-muted);">Trading Bot</p>
    </div>

    <!-- Nav -->
    <nav class="flex-1 py-4 space-y-0.5 px-2">
      {#each navItems as item}
        <a href={item.href}
          class="flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors"
          style={isActive(item.href)
            ? 'background: rgba(59,130,246,0.15); color: #3b82f6;'
            : 'color: var(--color-muted);'}
          onmouseenter={(e) => { if (!isActive(item.href)) (e.currentTarget as HTMLElement).style.background = 'rgba(255,255,255,0.04)'; (e.currentTarget as HTMLElement).style.color = 'var(--color-text)'; }}
          onmouseleave={(e) => { if (!isActive(item.href)) { (e.currentTarget as HTMLElement).style.background = ''; (e.currentTarget as HTMLElement).style.color = 'var(--color-muted)'; } }}>
          <span class="text-base w-5 text-center">{item.icon}</span>
          {item.label}
        </a>
      {/each}
    </nav>

    <!-- Backend status -->
    <div class="px-5 py-4 border-t" style="border-color: var(--color-border);">
      <div class="flex items-center gap-2">
        <span class="w-2 h-2 rounded-full {backendOnline ? 'bg-green-400' : 'bg-red-500'}"></span>
        <span class="text-xs" style="color: var(--color-muted);">
          {backendOnline ? 'Backend online' : 'Backend offline'}
        </span>
      </div>
    </div>
  </aside>

  <!-- ─── Main content area ─────────────────────────────────── -->
  <div class="flex-1 flex flex-col min-w-0">
    <!-- Halted warning banner -->
    {#if settings?.halted}
      <div class="flex items-center gap-3 px-4 py-2.5 text-sm font-medium"
        style="background: rgba(239,68,68,0.12); border-bottom: 1px solid rgba(239,68,68,0.3); color: #ef4444;">
        <span>🔴</span>
        <span>TRADING HALTED — All order execution is paused.</span>
        <a href="/settings" class="ml-auto underline text-xs">Resume →</a>
      </div>
    {/if}

    <main class="flex-1 p-4 md:p-6">
      {@render children()}
    </main>

    <!-- ─── Bottom nav (mobile) ──────────────────────────────── -->
    <nav class="md:hidden flex border-t"
      style="background: var(--color-surface); border-color: var(--color-border);">
      {#each navItems as item}
        <a href={item.href}
          class="flex-1 flex flex-col items-center gap-0.5 py-2 text-xs transition-colors"
          style={isActive(item.href) ? 'color: #3b82f6;' : 'color: var(--color-muted);'}>
          <span class="text-base">{item.icon}</span>
          <span>{item.label}</span>
        </a>
      {/each}
    </nav>
  </div>
</div>
