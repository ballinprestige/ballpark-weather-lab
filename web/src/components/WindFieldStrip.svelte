<script lang="ts">
  import type { BallparkGame } from '../lib/types';
  import { formatDelta, formatTime, isGameHeld } from '../lib/format';

  export let games: BallparkGame[];
  export let onOpen: (key: string) => void;

  const baselineY = 104;

  $: width = games.length === 1 ? 420 : 1200;

  const movementPercent = (game: BallparkGame): number | null => {
    if (isGameHeld(game)) return null;
    return (game.factors.weather_multiplier_runs - 1) * 100;
  };

  const xFor = (index: number): number => games.length === 1
    ? width / 2
    : 70 + index * ((width - 140) / (games.length - 1));

  const yFor = (game: BallparkGame): number => {
    const movement = movementPercent(game);
    return movement == null ? baselineY : baselineY - Math.max(-12, Math.min(12, movement)) * 4.2;
  };

  const tone = (game: BallparkGame): 'lift' | 'drag' | 'neutral' | 'hold' => {
    const movement = movementPercent(game);
    if (movement == null) return 'hold';
    if (movement >= 3) return 'lift';
    if (movement <= -3) return 'drag';
    return 'neutral';
  };

  function openDetails(event: MouseEvent, key: string): void {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    onOpen(key);
  }
</script>

<section class="wind-field" class:single-game={games.length === 1} aria-labelledby="wind-field-title">
  <header class="wind-field-heading">
    <div>
      <p class="eyebrow">Weather effect compared with each park baseline</p>
      <h2 id="wind-field-title">Slate wind comparison</h2>
    </div>
    <div class="wind-key" aria-label="Chart legend">
      <span><i class="key-line"></i>carry wind</span>
      <span><i class="key-zero"></i>park baseline</span>
    </div>
  </header>
  {#if games.length > 1}<p class="mobile-scroll-cue">Scroll horizontally to compare every game.</p>{/if}
  <!-- svelte-ignore a11y_no_noninteractive_tabindex (the overflowing chart is keyboard-scrollable) -->
  <div class="wind-field__scroller" role="region" tabindex="0" aria-label="Scrollable slate wind comparison">
    <svg viewBox={`0 0 ${width} 210`} aria-labelledby="wind-field-title wind-field-description">
      <desc id="wind-field-description">Every game positioned above or below its own park baseline, with verified carry wind shown in blue.</desc>
      <line x1="38" x2={width - 38} y1={baselineY} y2={baselineY} class="strip-baseline"></line>
      {#each [54, 79, 129, 154] as y}
        <line x1="38" x2={width - 38} y1={y} y2={y} class="strip-grid"></line>
      {/each}
      <text x="12" y="58" class="strip-scale">+12%</text>
      <text x="12" y="83" class="strip-scale">+6%</text>
      <text x="12" y={baselineY + 4} class="strip-scale">BASE</text>
      <text x="12" y="133" class="strip-scale">−6%</text>
      <text x="12" y="158" class="strip-scale">−12%</text>

      {#each games as game, index (game.game_pk)}
        {@const x = xFor(index)}
        {@const y = yFor(game)}
        {@const movement = movementPercent(game)}
        {@const state = tone(game)}
        <a
          href={`#game/${game.game_pk}`}
          class="station-link"
          aria-label={`${game.away_team} at ${game.home_team}, open game details`}
          on:click={(event) => openDetails(event, String(game.game_pk))}
        >
          <g transform={`translate(${x} 0)`} class:dome={game.weather.dome_active} data-tone={state}>
            <rect x="-39" y="35" width="78" height="125" class="station-band"></rect>
            <line x1="0" x2="0" y1="36" y2="160" class="station-rule"></line>
            {#if movement != null}
              <line x1="0" x2="0" y1={baselineY} y2={y} class="delta-stem"></line>
              <circle cx="0" cy={y} r="5.5" class="station-dot"></circle>
            {:else}
              <rect x="-8" y={baselineY - 8} width="16" height="16" class="missing-block"></rect>
            {/if}
            {#if !game.weather.dome_active && !isGameHeld(game)}
              <line x1="-18" y1={y - 15} x2="18" y2={y - 15} class="wind-arrow"></line>
              <path d={`M 18 ${y - 15} l -7 -5 m 7 5 l -7 5`} class="wind-arrow"></path>
              <text x="0" y="24" class="station-value">{formatDelta(game.weather.wind_carry_mph, 1)} mph</text>
            {:else if game.weather.dome_active}
              <text x="0" y="24" class="station-value">roof</text>
            {:else}
              <text x="0" y="24" class="station-value">held</text>
            {/if}
            <text x="0" y="174" class="station-team">{game.away_team}·{game.home_team}</text>
            <text x="0" y="188" class="station-delta">{movement == null ? '—' : formatDelta(movement, 0) + '%'}</text>
            <text x="0" y="201" class="station-time">{formatTime(game.game_time).replace(/\s[A-Z]{3,4}$/, '')}</text>
          </g>
        </a>
      {/each}
    </svg>
  </div>
</section>
