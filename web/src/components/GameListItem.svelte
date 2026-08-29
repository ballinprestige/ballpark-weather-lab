<script lang="ts">
  import type { BallparkGame, GeometryArtifact } from '../lib/types';
  import { findVenueGeometry, wallPath } from '../lib/geometry';
  import { formatDelta, formatFactor, formatTime, gameHoldReason, isGameHeld, teamLabel } from '../lib/format';

  export let game: BallparkGame;
  export let geometry: GeometryArtifact | null = null;
  export let rank: number | null = null;
  export let onOpen: () => void;

  $: held = isGameHeld(game);
  $: movement = held ? null : (game.factors.weather_multiplier_runs - 1) * 100;
  $: seasonal = (game.factors.seasonal_pf_runs - 1) * 100;
  $: gameVsAverage = (game.factors.game_pf_runs - 1) * 100;
  $: venueGeometry = findVenueGeometry(geometry, game.home_team);
  $: glyphPath = venueGeometry && geometry ? wallPath(geometry.angles_deg, venueGeometry.wall_distance_ft) : '';
  $: direction = movement == null
    ? 'Model reading unavailable'
    : movement >= 3
      ? 'Above park baseline'
      : movement <= -3
        ? 'Below park baseline'
        : 'Near park baseline';
  $: consequence = movement == null
    ? gameHoldReason(game)
    : `Game-hour conditions move this park’s run factor about ${Math.abs(Math.round(movement))}% ${movement > 0 ? 'above' : movement < 0 ? 'below' : 'along'} its seasonal baseline.`;

  function openDetails(event: MouseEvent): void {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    onOpen();
  }
</script>

<article class="game-card" data-tone={held ? 'hold' : movement != null && movement >= 3 ? 'lift' : movement != null && movement <= -3 ? 'drag' : 'neutral'}>
  <header class="game-card__header">
    <div class="venue-cell">
      {#if glyphPath}
        <svg class="park-glyph" viewBox="0 0 420 286" aria-hidden="true"><path d={glyphPath}></path></svg>
      {/if}
      <div>
        <p class="venue-name">{game.venue}</p>
        <p class="game-time">{formatTime(game.game_time)}</p>
      </div>
    </div>
    {#if game.weather.dome_active}<span class="roof-badge">Roof active</span>{/if}
  </header>

  {#if rank !== null && movement !== null}
    <div class="card-rankline">
      <span>0{rank}</span>
      <span>{rank === 1 ? 'largest movement' : 'movement watch'}</span>
      <strong>{formatDelta(movement, 0)}% vs park baseline</strong>
    </div>
  {/if}

  <h3>
    <a
      href={`#game/${game.game_pk}`}
      data-game-key={game.game_pk}
      aria-label={`Open ${teamLabel(game.away_team)} at ${teamLabel(game.home_team)} details, ${formatTime(game.game_time)}`}
      on:click={openDetails}
    >
      <span class="matchup"><strong>{game.away_team}</strong><span>at</span><strong>{game.home_team}</strong></span>
      <span class="inspect-cue" aria-hidden="true">↗</span>
    </a>
  </h3>

  <div class="game-card-body">
    <div class="card-verdict" data-tone={held ? 'hold' : movement != null && movement >= 3 ? 'lift' : movement != null && movement <= -3 ? 'drag' : 'neutral'}>
      <span class="verdict-dot" aria-hidden="true"></span>
      <strong>{direction}</strong>
    </div>

    {#if movement !== null}
      <div class="delta-track" aria-label={`${formatDelta(movement, 0)} percent versus this park's baseline`}>
        <span class="delta-zero"></span>
        <i class:positive={movement > 0} class:negative={movement < 0} class:neutral={movement === 0} style={`--magnitude:${Math.min(100, Math.abs(movement) * 5)}%`}></i>
        <small>this park’s baseline</small>
      </div>
    {/if}

    <p class="card-consequence">{consequence}</p>

    <dl class="card-factor-stack">
      <div><dt>This park normally</dt><dd>{formatDelta(seasonal, 0)}%</dd><small>vs MLB average</small></div>
      <div><dt>Weather adjusts</dt><dd>{movement == null ? '—' : formatDelta(movement, 0) + '%'}</dd><small>for game-hour conditions</small></div>
      <div><dt>Game-time park factor</dt><dd>{held ? '—' : formatDelta(gameVsAverage, 0) + '%'}</dd><small>vs MLB average</small></div>
    </dl>

    <dl class="card-support">
      <div>
        <dt>Home-run factor</dt>
        <dd>{held ? 'not available' : formatFactor(game.factors.game_pf_hr)}</dd>
        <small>neutral is 1.000</small>
      </div>
      <div class="wind">
        <dt>Wind</dt>
        <dd>{held ? 'held' : formatDelta(game.weather.wind_carry_mph, 1) + ' mph'}</dd>
        <small>{game.weather.dome_active ? 'roof — wind suspended' : 'carry component'}</small>
      </div>
    </dl>

    <footer class="card-evidence">
      <span class="ledger-state" data-tone={held ? 'hold' : 'good'}>{held ? 'Weather hold' : 'Verified'}</span>
      <span>{held ? 'weather values withheld' : `${Math.round(game.weather.temperature_f)}°F · ${Math.round(game.weather.humidity_pct)}%`}</span>
      <span class="lineup-glyph" class:confirmed={game.lineup.state === 'confirmed'}><i></i><i></i><i></i>{game.lineup.state === 'confirmed' ? 'Confirmed' : 'Pending'}</span>
      <span class="compact-factor"><small>RUNS PF</small> {held ? '—' : formatFactor(game.factors.game_pf_runs)}</span>
    </footer>
  </div>
</article>
