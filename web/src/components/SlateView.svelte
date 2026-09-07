<script lang="ts">
  import { onMount } from 'svelte';
  import type { BallparkGame, BallparkPayload } from '../lib/types';
  import { formatDate, formatDelta, formatTime, formatTimestamp, gameHoldReason, isGameHeld, teamLabel } from '../lib/format';
  import GameListItem from './GameListItem.svelte';
  import WindFieldStrip from './WindFieldStrip.svelte';
  import { assessOddsFreshness } from '../lib/freshness';

  type SlateFilter = 'all' | 'open' | 'roof' | 'incomplete';
  type SlateSort = 'movement' | 'time' | 'wind' | 'venue';

  export let payload: BallparkPayload;
  export let onOpenGame: (key: string) => void;
  export let now = new Date();

  let desktop = false;
  let filter: SlateFilter = 'all';
  let sort: SlateSort = 'movement';

  const movementPercent = (game: BallparkGame): number | null => {
    if (isGameHeld(game)) return null;
    return (game.factors.weather_multiplier_runs - 1) * 100;
  };

  const filtered = (game: BallparkGame, activeFilter: SlateFilter): boolean => {
    if (activeFilter === 'open') return game.weather.roof_state === 'open-air' && !isGameHeld(game);
    if (activeFilter === 'roof') return game.weather.dome_active || game.weather.roof_state === 'fixed-roof';
    if (activeFilter === 'incomplete') return isGameHeld(game);
    return true;
  };

  const sorted = (games: BallparkGame[], activeSort: SlateSort): BallparkGame[] => [...games].sort((left, right) => {
    const leftHeld = isGameHeld(left);
    const rightHeld = isGameHeld(right);
    if (leftHeld !== rightHeld) return leftHeld ? 1 : -1;
    if (activeSort === 'time') return Date.parse(left.game_time) - Date.parse(right.game_time);
    if (activeSort === 'wind') return Math.abs(right.weather.wind_carry_mph) - Math.abs(left.weather.wind_carry_mph);
    if (activeSort === 'venue') return left.venue.localeCompare(right.venue);
    const leftMovement = movementPercent(left);
    const rightMovement = movementPercent(right);
    if (leftMovement === null) return rightMovement === null ? 0 : 1;
    if (rightMovement === null) return -1;
    return Math.abs(rightMovement) - Math.abs(leftMovement);
  });

  const american = (price: number | null): string => price === null ? '—' : `${price > 0 ? '+' : ''}${price}`;

  const windContext = (game: BallparkGame): string => {
    if (isGameHeld(game)) return 'Weather held';
    if (game.weather.dome_active || game.weather.roof_state === 'fixed-roof') return 'Roof active';
    const carry = game.weather.wind_carry_mph;
    const cross = game.weather.wind_cross_mph;
    const direction = carry >= 2 ? 'Out' : carry <= -2 ? 'In' : Math.abs(cross) >= 2 ? 'Cross' : 'Light';
    return `${direction} · ${formatDelta(carry, 1)} carry · ${Math.abs(cross).toFixed(1)} cross`;
  };

  const exchangeAsk = (game: BallparkGame): string | null => {
    const market = game.exchange_market;
    if (!market || market.state !== 'observed_unknown_age' || market.line === null || market.over_ask_cents === null || market.under_ask_cents === null) return null;
    return `${market.game_phase.replaceAll('_', ' ')} · source age unknown · O ${market.line} YES ${market.over_ask_cents}c (${market.over_ask_dollars}) · U ${market.line} NO ${market.under_ask_cents}c (${market.under_ask_dollars})`;
  };

  function openBoardDetails(event: MouseEvent, key: string): void {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    onOpenGame(key);
  }

  onMount(() => {
    const media = window.matchMedia('(min-width: 44rem)');
    const update = () => desktop = media.matches;
    update();
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  });

  $: games = sorted(payload.games.filter((game) => filtered(game, filter)), sort);
  $: verified = payload.games.filter((game) => !isGameHeld(game)).length;
  $: currentSportsbook = payload.games.filter((game) => assessOddsFreshness(game.odds, now).state === 'current').length;
  $: knownSportsbook = payload.games.filter((game) => assessOddsFreshness(game.odds, now).state !== 'unavailable').length;
  $: marketSummary = knownSportsbook === 0
    ? `Sportsbook totals unavailable ${payload.games.length}/${payload.games.length}`
    : `Current sportsbook totals ${currentSportsbook}/${payload.games.length}`;
</script>

<section class="view slate-view" aria-labelledby="slate-title">
  <section class="slate-intro">
    <div>
      <p class="eyebrow">{formatDate(payload.date)} · {payload.games.length} {payload.games.length === 1 ? 'game' : 'games'}</p>
      <h1 id="slate-title">Ballpark board</h1>
      <p class="slate-meta">Weather ready {verified}/{payload.games.length} · {marketSummary} · Updated {formatTimestamp(payload.generated_at)}</p>
    </div>
  </section>

  <section class="slate-controls" aria-label="Slate controls">
    <div class="control-group">
      <span class="control-label">Filter</span>
      <div class="pill-row" role="group" aria-label="Filter games">
        {#each [['all', 'All'], ['open', 'Open air'], ['roof', 'Roof'], ['incomplete', 'Incomplete']] as option}
          <button class:active={filter === option[0]} aria-pressed={filter === option[0]} type="button" on:click={() => filter = option[0] as SlateFilter}>{option[1]}</button>
        {/each}
      </div>
    </div>
    <label class="sort-control">
      <span class="control-label">Sort</span>
      <select bind:value={sort}>
        <option value="movement">Largest movement</option>
        <option value="time">First pitch</option>
        <option value="wind">Carry wind</option>
        <option value="venue">Venue</option>
      </select>
    </label>
  </section>

  <h2 class="visually-hidden" id="games-heading">Games</h2>

  {#if games.length === 0}
    <div class="slate-empty">
      <strong>No games match this filter.</strong>
      <button type="button" on:click={() => filter = 'all'}>Show the full slate</button>
    </div>
  {:else if desktop}
    <p class="board-definition">Weather adjustment changes this park’s normal run environment; it is not a game-score forecast.</p>
    <div class="ledger-wrap">
      <table class="ledger">
        <thead>
          <tr><th>Matchup / first pitch</th><th>Total · Over / Under · book</th><th>Park wind</th><th>Weather adjustment</th></tr>
        </thead>
        <tbody>
          {#each games as game (game.game_pk)}
            {@const key = String(game.game_pk)}
            {@const movement = movementPercent(game)}
            {@const market = assessOddsFreshness(game.odds, now)}
            <tr data-tone={isGameHeld(game) ? 'hold' : 'ready'}>
              <td><a class="board-matchup" href={`#game/${key}`} data-game-key={game.game_pk} aria-label={`Open ${teamLabel(game.away_team)} at ${teamLabel(game.home_team)} details`} on:click={(event) => openBoardDetails(event, key)}><strong>{game.away_team} <i>at</i> {game.home_team}</strong><small>{formatTime(game.game_time)} · {game.venue}</small></a></td>
              <td>{#if exchangeAsk(game)}<strong>Kalshi contract ask</strong><small class="exchange-ask">{exchangeAsk(game)}</small><small class="market-secondary">Sportsbook unavailable</small>{:else if market.state === 'unavailable'}<strong>Sportsbook unavailable</strong>{:else}<strong>{game.odds.line}</strong> <span>O {american(game.odds.over_price)} · U {american(game.odds.under_price)}</span><br /><small>{game.odds.sportsbook_name} · {market.state === 'observed' ? 'observed, age unverified' : market.state}</small>{/if}</td>
              <td>{windContext(game)}</td>
              <td>{movement == null ? 'Held' : `${formatDelta(movement, 0)}% runs`}<br /><small>{isGameHeld(game) ? gameHoldReason(game) : `${Math.round(game.weather.temperature_f)}°F · ${Math.round(game.weather.humidity_pct)}%`}</small></td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {:else}
    <div class="game-grid">
      {#each games as game, index (game.game_pk)}
        {@const key = String(game.game_pk)}
        <div class="game-cell">
          <GameListItem
            {game}
            {now}
            onOpen={() => onOpenGame(key)}
          />
        </div>
      {/each}
    </div>
  {/if}

  {#if games.length}
    <WindFieldStrip {games} onOpen={onOpenGame} />
  {/if}
</section>
