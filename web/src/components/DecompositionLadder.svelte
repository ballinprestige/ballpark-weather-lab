<script lang="ts">
  import type { BallparkGame } from '../lib/types';
  import { formatDelta, formatFactor } from '../lib/format';
  import { isReadyState } from '../lib/validate';

  export let game: BallparkGame;

  $: ready = isReadyState(game.factors.state);
  $: rows = [
    {
      label: 'Runs',
      seasonal: game.factors.seasonal_pf_runs,
      multiplier: game.factors.weather_multiplier_runs,
      game: game.factors.game_pf_runs,
      delta: game.factors.weather_delta_runs
    },
    {
      label: 'Home runs',
      seasonal: game.factors.seasonal_pf_hr,
      multiplier: game.factors.weather_multiplier_hr,
      game: game.factors.game_pf_hr,
      delta: game.factors.weather_delta_hr
    }
  ];

  function interpretation(value: number | null): string {
    if (value === null) return 'not available';
    if (Math.abs(value - 1) < 0.005) return 'near neutral';
    return value > 1 ? 'above neutral' : 'below neutral';
  }
</script>

<section class="evidence-section decomposition" aria-labelledby={`decomposition-${game.game_pk}`}>
  <div class="section-heading">
    <div>
      <p class="eyebrow">Approach B</p>
      <h3 id={`decomposition-${game.game_pk}`}>Decomposition ladder</h3>
    </div>
    <span class="section-note">Neutral = 1.000</span>
  </div>

  {#if ready}
    <div class="ladder-table" role="table" aria-label="Park-factor decomposition">
      <div class="ladder-row ladder-head" role="row">
        <span role="columnheader">Metric</span>
        <span role="columnheader">Seasonal park</span>
        <span role="columnheader">Weather multiplier</span>
        <span role="columnheader">Game factor</span>
      </div>
      {#each rows as row}
        <div class="ladder-row" role="row">
          <span role="rowheader"><strong>{row.label}</strong></span>
          <span role="cell" class="ladder-value">{formatFactor(row.seasonal)}</span>
          <span role="cell" class="ladder-operation"><span aria-hidden="true">×</span> {formatFactor(row.multiplier)}</span>
          <span role="cell" class="ladder-result">
            <strong>{formatFactor(row.game)}</strong>
            <small>{interpretation(row.game)} · Δ {formatDelta(row.delta)}</small>
          </span>
        </div>
      {/each}
    </div>
    <p class="plain-note">The game factor is the venue baseline adjusted by game-hour weather. It is park context, not a score forecast.</p>
  {:else}
    <div class="figure-hold">
      <span class="hold-hatch" aria-hidden="true"></span>
      <p><strong>Factors held.</strong> {game.factors.reason ?? 'A validated weather-adjusted factor is not available for this game.'}</p>
    </div>
  {/if}
</section>
