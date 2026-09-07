<script lang="ts">
  import type { BallparkGame } from '../lib/types';
  import { formatDelta, formatTime, gameHoldReason, isGameHeld, teamLabel } from '../lib/format';
  import { assessOddsFreshness } from '../lib/freshness';

  export let game: BallparkGame;
  export let onOpen: () => void;
  export let now = new Date();

  $: held = isGameHeld(game);
  $: market = assessOddsFreshness(game.odds, now);
  $: wind = held ? 'Weather held' : game.weather.dome_active ? 'Roof active' : `${formatDelta(game.weather.wind_carry_mph, 1)} mph carry`;
  $: weather = held ? gameHoldReason(game) : `${Math.round(game.weather.temperature_f)}°F · ${Math.round(game.weather.humidity_pct)}%`;
  const american = (price: number | null): string => price === null ? '—' : `${price > 0 ? '+' : ''}${price}`;

  function openDetails(event: MouseEvent): void {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    onOpen();
  }
</script>

<article class="game-card compact-game-row" data-tone={held ? 'hold' : market.state}>
  <a href={`#game/${game.game_pk}`} data-game-key={game.game_pk} aria-label={`Open ${teamLabel(game.away_team)} at ${teamLabel(game.home_team)} details, ${formatTime(game.game_time)}`} on:click={openDetails}>
    <span class="compact-matchup"><strong>{game.away_team}</strong><i>at</i><strong>{game.home_team}</strong><small>{formatTime(game.game_time)} · {game.venue}</small></span>
    <span class="compact-market">
      {#if market.state === 'unavailable'}<strong>Market unavailable</strong><small>{market.reason ?? 'No verified quote.'}</small>
      {:else}<strong>{game.odds.line}</strong><small>O {american(game.odds.over_price)} · U {american(game.odds.under_price)} · {game.odds.sportsbook_name}{market.state === 'observed' ? ' · observed, age unverified' : ''}</small>{/if}
    </span>
    <span class="compact-weather"><strong>{wind}</strong><small>{weather}</small></span>
  </a>
</article>
