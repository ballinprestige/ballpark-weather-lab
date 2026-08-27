<script lang="ts">
  import { onMount, tick } from 'svelte';
  import type { ArchiveEntry, BallparkPayload, PublicationBundle } from './lib/types';
  import { loadArchivePublication, loadCurrentPublication } from './lib/data';
  import { formatDate, formatTimestamp, shortHash } from './lib/format';
  import AppHeader, { type ViewName } from './components/AppHeader.svelte';
  import DataHealth from './components/DataHealth.svelte';
  import HistoryView from './components/HistoryView.svelte';
  import MethodView from './components/MethodView.svelte';
  import SlateView from './components/SlateView.svelte';
  import StatePanel from './components/StatePanel.svelte';

  const views = new Set<ViewName>(['slate', 'health', 'history', 'method']);

  let route: ViewName = 'slate';
  let theme: 'day' | 'night' = 'day';
  let bundle: PublicationBundle | null = null;
  let payload: BallparkPayload | null = null;
  let payloadHash = '';
  let loading = true;
  let error: string | null = null;
  let archiveError: string | null = null;
  let loadingArchiveDate: string | null = null;
  let isArchive = false;
  let loadSequence = 0;

  onMount(() => {
    const storedTheme = localStorage.getItem('ballpark-theme');
    if (storedTheme === 'day' || storedTheme === 'night') theme = storedTheme;
    else theme = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'night' : 'day';
    applyTheme();
    routeFromHash(false);
    window.addEventListener('hashchange', handleHashChange);
    void loadPublication();
    return () => window.removeEventListener('hashchange', handleHashChange);
  });

  function applyTheme(): void {
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'day' ? '#f0eadb' : '#171b18');
  }

  function toggleTheme(): void {
    theme = theme === 'day' ? 'night' : 'day';
    localStorage.setItem('ballpark-theme', theme);
    applyTheme();
  }

  function routeFromHash(moveFocus = true): void {
    const candidate = window.location.hash.replace('#', '') as ViewName;
    route = views.has(candidate) ? candidate : 'slate';
    if (!views.has(candidate) && window.location.hash) history.replaceState(null, '', '#slate');
    if (moveFocus) void focusViewHeading();
  }

  function handleHashChange(): void {
    routeFromHash(true);
  }

  async function focusViewHeading(): Promise<void> {
    await tick();
    const heading = document.querySelector<HTMLElement>('#main-content h1');
    if (heading) {
      heading.tabIndex = -1;
      heading.focus({ preventScroll: true });
    }
  }

  async function loadPublication(): Promise<void> {
    const sequence = ++loadSequence;
    loading = true;
    error = null;
    try {
      const loaded = await loadCurrentPublication();
      if (sequence !== loadSequence) return;
      bundle = loaded;
      payload = loaded.payload;
      payloadHash = loaded.payloadHash;
      isArchive = false;
    } catch (reason) {
      if (sequence !== loadSequence) return;
      error = reason instanceof Error ? reason.message : 'The publication could not be loaded.';
    } finally {
      if (sequence === loadSequence) loading = false;
    }
  }

  async function openArchive(entry: ArchiveEntry): Promise<void> {
    archiveError = null;
    loadingArchiveDate = entry.date;
    try {
      const loaded = await loadArchivePublication(entry);
      payload = loaded.payload;
      payloadHash = loaded.payloadHash;
      isArchive = entry.date !== bundle?.payload.date;
      window.location.hash = 'slate';
    } catch (reason) {
      archiveError = reason instanceof Error ? reason.message : 'The archive snapshot could not be loaded.';
    } finally {
      loadingArchiveDate = null;
    }
  }

  function returnLive(): void {
    if (!bundle) return;
    payload = bundle.payload;
    payloadHash = bundle.payloadHash;
    isArchive = false;
    archiveError = null;
  }

  function showHealth(): void {
    window.location.hash = 'health';
  }
</script>

<svelte:head>
  <title>{route === 'slate' ? 'Slate' : route === 'health' ? 'Data Health' : route === 'history' ? 'History' : 'Method'} · Ballpark Weather Lab</title>
</svelte:head>

<a class="skip-link" href="#main-content">Skip to main content</a>
<AppHeader current={route} {theme} onThemeToggle={toggleTheme} />

{#if payload}
  <aside class="publication-ribbon" data-status={payload.status} aria-label="Publication status">
    <div>
      <strong>{isArchive ? 'ARCHIVE' : payload.status === 'ready' ? 'READY' : payload.status === 'degraded' ? 'DEGRADED' : 'NO SLATE'}</strong>
      <span>{formatDate(payload.date)}</span>
    </div>
    <p>{isArchive ? 'Historical snapshot' : payload.status === 'degraded' ? 'Some games or optional context are held; ready games remain visible.' : payload.status === 'no_slate' ? (payload.no_slate_reason ?? 'No games are scheduled for this date.') : 'Validated daily park-weather slate.'}</p>
    <div class="ribbon-receipt">
      <span>Updated {formatTimestamp(payload.generated_at)}</span>
      <code title={payloadHash}>SHA {shortHash(payloadHash)}</code>
    </div>
    {#if isArchive}<button type="button" on:click={returnLive}>Return to current release</button>{/if}
  </aside>
{/if}

<main id="main-content" tabindex="-1">
  {#if loading}
    <section class="loading-sheet" aria-live="polite" aria-busy="true">
      <div class="loading-field" aria-hidden="true"></div>
      <p class="eyebrow">Reading public release</p>
      <h1>Checking date, schema, and payload hash…</h1>
    </section>
  {:else if error}
    <StatePanel
      eyebrow="Publication unavailable"
      title="The field sheet did not pass readback"
      message={`${error} No unverified values are shown.`}
      actionLabel="Check the release again"
      onAction={loadPublication}
    />
  {:else if payload && bundle}
    {#if route === 'slate'}
      {#if payload.status === 'no_slate'}
        <StatePanel
          eyebrow="Schedule confirmed"
          title="No games on the slate"
          message={`${payload.no_slate_reason ?? `The schedule source reports no games for ${formatDate(payload.date)}.`} This is a valid publication, not a loading error.`}
          actionLabel="Review Data Health"
          onAction={showHealth}
        />
      {:else}
        <SlateView {payload} geometry={bundle.geometry} />
      {/if}
    {:else if route === 'health'}
      <DataHealth {payload} {payloadHash} geometry={bundle.geometry} warnings={bundle.warnings} {isArchive} />
    {:else if route === 'history'}
      <HistoryView
        archive={bundle.archive}
        currentDate={bundle.payload.date}
        selectedDate={payload.date}
        loadingDate={loadingArchiveDate}
        error={archiveError}
        onSelect={openArchive}
        onReturnLive={returnLive}
      />
    {:else}
      <MethodView {payload} />
    {/if}
  {/if}
</main>

<footer class="site-footer">
  <div>
    <strong>Ballpark Weather Lab</strong>
    <span>Open, inspectable park context for every game on the slate.</span>
    <span>Weather data by <a href="https://open-meteo.com/" rel="noopener noreferrer">Open-Meteo</a>.</span>
  </div>
  <p>Factors describe venue and weather conditions around a neutral value of 1.000. They are not outcome forecasts.</p>
</footer>
