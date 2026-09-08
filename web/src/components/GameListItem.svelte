<script lang="ts">
  import type { BallparkGame } from '../lib/types';
  import { formatContractCents, formatDelta, formatTime, gameHoldReason, isGameHeld, isModelAdjustmentHeld, isWeatherHeld, teamLabel } from '../lib/format';
  import { assessOddsFreshness } from '../lib/freshness';
  import { effectiveExchangePhase } from '../lib/exchange';

  export let game: BallparkGame;
  export let onOpen: () => void;
  export let now = new Date();

  $: held = isGameHeld(game);
  $: weatherHeld = isWeatherHeld(game);
  $: modelHeld = !weatherHeld && isModelAdjustmentHeld(game);
  $: market = assessOddsFreshness(game.odds, now);
  $: wind = weatherHeld
    ? 'Weather held'
    : game.weather.dome_active || game.weather.roof_state === 'fixed-roof'
      ? 'Roof active'
      : `${game.weather.wind_carry_mph >= 2 ? 'Out' : game.weather.wind_carry_mph <= -2 ? 'In' : Math.abs(game.weather.wind_cross_mph) >= 2 ? 'Cross' : 'Light'} · ${formatDelta(game.weather.wind_carry_mph, 1)} carry`;
  $: weather = weatherHeld
    ? gameHoldReason(game)
    : modelHeld
      ? `Model adjustment held · ${gameHoldReason(game)}`
      : `${Math.round(game.weather.temperature_f)}°F · ${Math.round(game.weather.humidity_pct)}%`;
  $: exchange = game.exchange_market?.state === 'observed_unknown_age'
    && game.exchange_market.line !== null
    && game.exchange_market.over_ask_cents !== null
    && game.exchange_market.under_ask_cents !== null
      ? game.exchange_market
      : null;
  $: exchangePhase = effectiveExchangePhase(exchange, game.game_time, now);
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
      {#if market.state !== 'unavailable'}<strong>{game.odds.line}</strong><small>O {american(game.odds.over_price)} / U {american(game.odds.under_price)} / {game.odds.sportsbook_name}{market.state === 'observed' ? ` via ${game.odds.provider} / captured ${formatTime(game.odds.observed_at)} / source age unknown` : ''}{game.odds.failure_reason ? ' / ESPN update failed · showing the captured prices' : ''}</small>{#if exchange}<small class="market-secondary">Kalshi total {exchange.line} / Over {formatContractCents(exchange.over_ask_cents)} / Under {formatContractCents(exchange.under_ask_cents)} / {exchangePhase?.replaceAll('_', ' ')} / captured {formatTime(exchange.observed_at)}{exchange.failure_reason ? ` / Update failed: ${exchange.failure_reason}` : ''}</small>{/if}
      {:else if exchange}<strong>{exchange.line}</strong><small>Over {formatContractCents(exchange.over_ask_cents)} / Under {formatContractCents(exchange.under_ask_cents)}</small><small class="market-secondary">Kalshi contract ask / {exchangePhase?.replaceAll('_', ' ')} / source age unknown / captured {formatTime(exchange.observed_at)}{exchange.failure_reason ? ` / Update failed: ${exchange.failure_reason}` : ''}</small>
      {:else}<strong>Sportsbook unavailable</strong>{/if}
    </span>
    <span class="compact-weather"><strong>{wind}</strong><small>{weather}</small></span>
  </a>
</article>
