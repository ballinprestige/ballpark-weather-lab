<script lang="ts">
  import { onMount, tick } from 'svelte';
  import type { ArchiveEntry, BallparkPayload, PublicationBundle } from './lib/types';
  import { loadArchivePublication, loadCurrentPublication } from './lib/data';
  import { formatDate, formatTimestamp, shortHash } from './lib/format';
  import { assessPublicationFreshness } from './lib/freshness';
  import AppHeader, { type ViewName } from './components/AppHeader.svelte';
  import DataHealth from './components/DataHealth.svelte';
  import GameDetail from './components/GameDetail.svelte';
  import HistoryView from './components/HistoryView.svelte';
  import MethodView from './components/MethodView.svelte';
  import SlateView from './components/SlateView.svelte';
  import StatePanel from './components/StatePanel.svelte';

  const views = new Set<ViewName>(['slate', 'health', 'history', 'method']);
  type RouteName = ViewName | 'game';

  let route: RouteName = 'slate';
  let routedGameKey = '';
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
  let currentInstant = new Date();
  let slateReturn: { gameKey: string; scrollY: number } | null = null;

  $: freshness = payload ? assessPublicationFreshness(payload.date, currentInstant) : null;
  $: publicationIsStale = Boolean(freshness?.isStale && !isArchive);
  $: routedGame = route === 'game' && payload
    ? payload.games.find((game) => String(game.game_pk) === routedGameKey) ?? null
    : null;

  onMount(() => {
    const storedTheme = localStorage.getItem('ballpark-theme');
    if (storedTheme === 'day' || storedTheme === 'night') theme = storedTheme;
    else theme = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'night' : 'day';
    applyTheme();
    routeFromHash(false);
    window.addEventListener('hashchange', handleHashChange);
    const freshnessTimer = window.setInterval(() => currentInstant = new Date(), 60_000);
    void loadPublication();
    return () => {
      window.removeEventListener('hashchange', handleHashChange);
      window.clearInterval(freshnessTimer);
    };
  });

  function applyTheme(): void {
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'day' ? '#f5f7fa' : '#0e141b');
  }

  function toggleTheme(): void {
    theme = theme === 'day' ? 'night' : 'day';
    localStorage.setItem('ballpark-theme', theme);
    applyTheme();
  }

  function routeFromHash(moveFocus = true): void {
    const previousRoute = route;
    const candidate = window.location.hash.replace(/^#/, '');
    const gameMatch = /^game\/(\d+)$/.exec(candidate);
    if (gameMatch) {
      route = 'game';
      routedGameKey = gameMatch[1];
    } else if (views.has(candidate as ViewName)) {
      route = candidate as ViewName;
      routedGameKey = '';
    } else {
      route = 'slate';
      routedGameKey = '';
      if (window.location.hash) history.replaceState(null, '', '#slate');
    }
    if (moveFocus) void settleRoute(previousRoute);
  }

  function handleHashChange(): void {
    routeFromHash(true);
  }

  function jumpTo(scrollY: number): void {
    const root = document.documentElement;
    const previousBehavior = root.style.scrollBehavior;
    root.style.scrollBehavior = 'auto';
    void root.offsetHeight;
    window.scrollTo(0, scrollY);
    window.requestAnimationFrame(() => root.style.scrollBehavior = previousBehavior);
  }

  async function settleRoute(previousRoute: RouteName): Promise<void> {
    if (route === 'game') jumpTo(0);
    await tick();
    if (route === 'slate' && previousRoute === 'game' && slateReturn) {
      const target = document.querySelector<HTMLElement>(`[data-game-key="${slateReturn.gameKey}"]`);
      jumpTo(slateReturn.scrollY);
      target?.focus({ preventScroll: true });
      slateReturn = null;
      return;
    }
    if (route !== 'game') jumpTo(0);
    const heading = document.querySelector<HTMLElement>('#main-content h1, #main-content h2');
    if (heading) {
      heading.tabIndex = -1;
      heading.focus({ preventScroll: true });
    }
    if (route !== 'game') slateReturn = null;
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

  function showSlate(): void {
    window.location.hash = 'slate';
  }

  function openGame(key: string): void {
    slateReturn = { gameKey: key, scrollY: window.scrollY };
    const previousRoute = route;
    history.pushState(null, '', `#game/${key}`);
    route = 'game';
    routedGameKey = key;
    void settleRoute(previousRoute);
  }
</script>

<svelte:head>
  <title>{publicationIsStale ? 'Stale release · ' : ''}{route === 'slate' ? 'Slate' : route === 'game' ? (routedGame ? `${routedGame.away_team} at ${routedGame.home_team}` : 'Game details') : route === 'health' ? 'Data Health' : route === 'history' ? 'History' : 'Method'} · Ballpark Weather Lab</title>
</svelte:head>

<a class="skip-link" href="#main-content">Skip to main content</a>
<AppHeader current={route === 'game' ? 'slate' : route} {theme} onThemeToggle={toggleTheme} />

{#if payload}
  <aside
    class="publication-ribbon"
    data-status={isArchive ? 'archive' : publicationIsStale ? 'stale' : payload.status}
    aria-label={publicationIsStale ? 'Stale publication warning' : 'Publication status'}
    aria-live={publicationIsStale ? 'assertive' : 'off'}
    aria-atomic="true"
    role={publicationIsStale ? 'alert' : undefined}
  >
    <div>
      <strong>{isArchive ? 'ARCHIVE' : publicationIsStale ? 'STALE' : payload.status === 'ready' ? 'READY' : payload.status === 'degraded' ? 'DEGRADED' : 'NO SLATE'}</strong>
      <span>{formatDate(payload.date)}</span>
    </div>
    <p>{isArchive ? 'Historical snapshot' : publicationIsStale && freshness ? `Showing ${formatDate(payload.date)} while the current MLB date is ${formatDate(freshness.currentDate)} (America/New_York). Do not treat this slate as current.${payload.status === 'degraded' ? ' The dated release also contains held data.' : payload.status === 'no_slate' ? ' The no-slate result applies only to the displayed date.' : ''}` : payload.status === 'degraded' ? 'Some games or optional context are held; ready games remain visible.' : payload.status === 'no_slate' ? (payload.no_slate_reason ?? 'No games are scheduled for this date.') : 'Validated daily park-weather slate.'}</p>
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
      <p class="eyebrow">Loading public release</p>
      <h1>Verifying date, schema, and payload hash…</h1>
    </section>
  {:else if error}
    <StatePanel
      eyebrow="Publication unavailable"
      title="Release verification failed"
      message={`${error} No unverified values are shown.`}
      actionLabel="Check the release again"
      onAction={loadPublication}
    />
  {:else if payload && bundle}
    {#if route === 'slate'}
      {#if payload.status === 'no_slate'}
        <StatePanel
          eyebrow="Schedule confirmed"
          title="No games scheduled"
          message={`${payload.no_slate_reason ?? `The schedule source reports no games for ${formatDate(payload.date)}.`} This is a valid publication, not a loading error.`}
          actionLabel="Review Data Health"
          onAction={showHealth}
        />
      {:else}
        <SlateView {payload} geometry={bundle.geometry} onOpenGame={openGame} />
      {/if}
    {:else if route === 'game'}
      {#if routedGame}
        <section class="game-route">
          <a class="station-back" href="#slate">← Back to daily park factors</a>
          <GameDetail game={routedGame} geometry={bundle.geometry} headingLevel={1} />
        </section>
      {:else}
        <StatePanel
          eyebrow="Details unavailable"
          title="That game is not in this release"
          message="The requested game ID does not appear in the validated slate. No substitute data is shown."
          actionLabel="Return to daily park factors"
          onAction={showSlate}
        />
      {/if}
    {:else if route === 'health'}
      <DataHealth {payload} {payloadHash} geometry={bundle.geometry} warnings={bundle.warnings} {isArchive} isStale={publicationIsStale} />
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
  <div class="footer-station">
    <span class="brand-mark mini" aria-hidden="true"><i></i><i></i><i></i></span>
    <div>
      <strong>Ballpark Weather Lab</strong>
      <small>MLB park and weather context.</small>
    </div>
  </div>
  <p class="legal">Factors describe venue and weather conditions around a neutral value of 1.000. They are not game-outcome forecasts.</p>
  <div class="footer-links">
    <a href="#history">Release archive</a>
    <a href="#method">Method and limitations</a>
    <span>Weather data: <a href="https://open-meteo.com/" rel="noopener noreferrer">Open-Meteo</a></span>
    <span>Standalone public application</span>
  </div>
</footer>
