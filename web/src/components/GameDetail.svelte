<script lang="ts">
  import type { BallparkGame, GeometryArtifact } from '../lib/types';
  import { displayValue, formatFactor, formatTime, formatTimestamp, gameHoldReason, isGameHeld, pitcherName, stateTone, teamLabel, windLabel } from '../lib/format';
  import { isReadyState } from '../lib/validate';
  import StateBadge from './StateBadge.svelte';
  import ParkWindDiagram from './ParkWindDiagram.svelte';
  import DecompositionLadder from './DecompositionLadder.svelte';
  import TrajectoryTheater from './TrajectoryTheater.svelte';

  export let game: BallparkGame;
  export let geometry: GeometryArtifact | null;
  export let headingLevel: 1 | 2 | 3 = 2;

  $: held = isGameHeld(game);
  $: weatherHeld = !isReadyState(game.weather.state);
  $: titleId = `game-detail-title-${game.game_pk}`;
  $: lineupReady = isReadyState(game.lineup.state);
</script>

<article class="game-detail" aria-labelledby={titleId} data-testid="game-detail">
  <header class="game-detail-header">
    <div class="detail-kicker">
      <span>{formatTime(game.game_time)}</span>
      <span aria-hidden="true">/</span>
      <span>{game.venue}</span>
    </div>
    {#if headingLevel === 1}
      <h1 id={titleId} aria-label={`${teamLabel(game.away_team)} at ${teamLabel(game.home_team)}`}><strong>{game.away_team}</strong> <span>at</span> <strong>{game.home_team}</strong></h1>
    {:else if headingLevel === 2}
      <h2 id={titleId} aria-label={`${teamLabel(game.away_team)} at ${teamLabel(game.home_team)}`}><strong>{game.away_team}</strong> <span>at</span> <strong>{game.home_team}</strong></h2>
    {:else}
      <h3 id={titleId} aria-label={`${teamLabel(game.away_team)} at ${teamLabel(game.home_team)}`}><strong>{game.away_team}</strong> <span>at</span> <strong>{game.home_team}</strong></h3>
    {/if}
    <div class="detail-status-line">
      <StateBadge state={game.game_status} />
      <span>{pitcherName(game, 'away')} vs {pitcherName(game, 'home')}</span>
    </div>
  </header>

  {#if held}
    <div class="game-hold" role="status" data-testid="weather-hold">
      <span class="hold-hatch" aria-hidden="true"></span>
      <div>
        <p class="eyebrow">Data unavailable</p>
        <h3>Weather-adjusted headline withheld</h3>
        <p>{gameHoldReason(game)} The rest of the slate remains available.</p>
      </div>
    </div>
  {:else}
    <section class="factor-headline" aria-labelledby={`headline-${game.game_pk}`}>
      <div>
        <p class="eyebrow">Game-hour park context</p>
        <h3 id={`headline-${game.game_pk}`}>Runs factor <strong>{formatFactor(game.factors.game_pf_runs)}</strong></h3>
        <p>Home-run factor {formatFactor(game.factors.game_pf_hr)} · neutral is 1.000</p>
      </div>
      <div class="factor-seal" data-tone={stateTone(game.factors.state)} aria-label={`Factor state: ${game.factors.state}`}>
        <span>PF</span>
        <strong>{formatFactor(game.factors.game_pf_runs)}</strong>
      </div>
    </section>
  {/if}

  <section class="conditions-strip" aria-labelledby={`conditions-${game.game_pk}`}>
    <div class="section-heading compact">
      <div>
        <p class="eyebrow">Observed conditions</p>
        <h3 id={`conditions-${game.game_pk}`}>Game-hour weather</h3>
      </div>
      <StateBadge state={game.weather.state} />
    </div>
    <dl class="reading-grid">
      <div><dt>Temperature</dt><dd>{weatherHeld ? '—' : `${game.weather.temperature_f.toFixed(0)}°F`}</dd></div>
      <div><dt>Humidity</dt><dd>{weatherHeld ? '—' : `${game.weather.humidity_pct.toFixed(0)}%`}</dd></div>
      <div><dt>Wind</dt><dd>{weatherHeld ? 'Withheld' : windLabel(game.weather)}</dd></div>
      <div><dt>Roof</dt><dd>{displayValue(game.weather.roof_state)}</dd></div>
    </dl>
    <p class="source-line">
      <span>{game.weather.source ?? 'Source not reported'}</span>
      <span>Basis: {game.weather.basis ?? 'not reported'}</span>
      <span>Valid {formatTimestamp(game.weather.valid_at)}</span>
    </p>
  </section>

  <ParkWindDiagram {game} {geometry} />
  <DecompositionLadder {game} />

  <section class="evidence-section evidence-receipt" aria-labelledby={`receipt-${game.game_pk}`}>
    <div class="section-heading">
      <div>
        <p class="eyebrow">Release inputs</p>
        <h3 id={`receipt-${game.game_pk}`}>Data behind this reading</h3>
      </div>
      <span class="section-note">Inspectable inputs</span>
    </div>
    <dl class="receipt-grid">
      <div><dt>Weather source</dt><dd>{game.weather.source ?? 'Not reported'}</dd></div>
      <div><dt>Weather valid</dt><dd>{formatTimestamp(game.weather.valid_at)}</dd></div>
      <div><dt>Evidence state</dt><dd>{held ? 'Weather-adjusted factor held' : 'Verified weather inputs'}</dd></div>
      <div><dt>Lineups</dt><dd>{game.lineup.state === 'confirmed' ? 'confirmed / confirmed' : game.lineup.state.replaceAll('_', ' ')}</dd></div>
      <div><dt>Approach B</dt><dd>{game.factors.state}</dd></div>
      <div><dt>Approach C</dt><dd>{game.approach_c.state.replaceAll('_', ' ')}</dd></div>
    </dl>
  </section>

  <section class="evidence-section lineup-context" aria-labelledby={`lineup-${game.game_pk}`}>
    <div class="section-heading">
      <div>
        <p class="eyebrow">Lineup state</p>
        <h3 id={`lineup-${game.game_pk}`}>Approach C context</h3>
      </div>
      <StateBadge state={game.approach_c.state} />
    </div>
    <div class="lineup-ledger">
      <div>
        <span>Away lineup</span>
        <strong>{game.lineup.away_count}/9</strong>
      </div>
      <div>
        <span>Home lineup</span>
        <strong>{game.lineup.home_count}/9</strong>
      </div>
      <div>
        <span>Headline use</span>
        <strong>{game.approach_c.used_in_headline ? 'Included' : 'Not used'}</strong>
      </div>
    </div>
    <p>{lineupReady ? `Lineups observed ${formatTimestamp(game.lineup.observed_at)}.` : (game.lineup.reason ?? 'Confirmed lineups are not yet available.')}</p>
    <p class="plain-note">{game.approach_c.reason ?? game.approach_c.method ?? 'Approach C is optional and never blocks the weather-adjusted park-factor slate.'}</p>
  </section>

  <TrajectoryTheater {game} />
</article>
