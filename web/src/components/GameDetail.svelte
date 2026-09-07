<script lang="ts">
  import type { BallparkGame, GeometryArtifact } from '../lib/types';
  import { displayValue, formatContractCents, formatFactor, formatTime, formatTimestamp, gameHoldReason, isGameHeld, pitcherName, stateTone, teamLabel, windLabel } from '../lib/format';
  import { isReadyState } from '../lib/validate';
  import { assessOddsFreshness } from '../lib/freshness';
  import StateBadge from './StateBadge.svelte';
  import ParkWindDiagram from './ParkWindDiagram.svelte';
  import DecompositionLadder from './DecompositionLadder.svelte';
  import TrajectoryTheater from './TrajectoryTheater.svelte';

  export let game: BallparkGame;
  export let geometry: GeometryArtifact | null;
  export let headingLevel: 1 | 2 | 3 = 2;
  export let now = new Date();

  $: held = isGameHeld(game);
  $: weatherHeld = !isReadyState(game.weather.state);
  $: titleId = `game-detail-title-${game.game_pk}`;
  $: lineupReady = isReadyState(game.lineup.state);
  $: marketFreshness = assessOddsFreshness(game.odds, now);
  $: exchangeAvailable = game.exchange_market?.state === 'observed_unknown_age'
    && game.exchange_market.line !== null
    && game.exchange_market.over_ask_cents !== null
    && game.exchange_market.under_ask_cents !== null;
  const american = (price: number | null): string => price === null ? '—' : `${price > 0 ? '+' : ''}${price}`;
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
    {#if exchangeAvailable}
      <p class="detail-exchange-line"><strong>{game.exchange_market?.line}</strong> · Over {formatContractCents(game.exchange_market?.over_ask_cents)} · Under {formatContractCents(game.exchange_market?.under_ask_cents)} <span>Kalshi · captured {formatTimestamp(game.exchange_market?.observed_at ?? null)}</span></p>
    {/if}
  </header>

  <ParkWindDiagram {game} {geometry} />

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

  {#if !exchangeAvailable || marketFreshness.state !== 'unavailable'}
  <section class="market-section" aria-labelledby={`market-${game.game_pk}`} data-state={marketFreshness.state}>
    <div class="section-heading">
      <div><p class="eyebrow">Full-game market evidence</p><h3 id={`market-${game.game_pk}`}>Total, Over &amp; Under</h3></div>
      <StateBadge state={marketFreshness.state} />
    </div>
    {#if marketFreshness.state === 'unavailable'}
      <p class="market-unavailable">{marketFreshness.reason ?? 'No verified full-game total is available. No line or price is substituted.'}</p>
    {:else}
      <dl class="market-grid">
        <div><dt>Total</dt><dd>{game.odds.line}</dd></div>
        <div><dt>Over</dt><dd>{american(game.odds.over_price)}</dd></div>
        <div><dt>Under</dt><dd>{american(game.odds.under_price)}</dd></div>
        <div><dt>Sportsbook</dt><dd>{game.odds.sportsbook_name}</dd></div>
      </dl>
      {#if marketFreshness.state === 'stale'}<p class="market-warning">Stale quote: {marketFreshness.reason}. It is not current.</p>{/if}
      {#if marketFreshness.state === 'observed'}<p class="market-warning">Observed quote: {marketFreshness.reason} It does not satisfy the current sportsbook freshness requirement.</p>{/if}
      <p class="source-line"><span>{game.odds.provider}</span><span>Source updated {formatTimestamp(game.odds.source_updated_at)}</span><span>Retrieved {formatTimestamp(game.odds.observed_at)}</span></p>
    {/if}
  </section>
  {/if}

  {#if exchangeAvailable}
    <section class="exchange-market" aria-labelledby={`exchange-${game.game_pk}`}>
      <div class="section-heading compact">
        <div><p class="eyebrow">Supplemental exchange evidence</p><h3 id={`exchange-${game.game_pk}`}>Kalshi contract asks</h3></div>
        <StateBadge state="observed_unknown_age" label="Observed · source age unknown" />
      </div>
      <dl class="market-grid exchange-grid">
        <div><dt>Total condition</dt><dd>Over {game.exchange_market?.line}</dd></div>
        <div><dt>YES ask</dt><dd>{formatContractCents(game.exchange_market?.over_ask_cents)} <small>({game.exchange_market?.over_ask_dollars})</small></dd></div>
        <div><dt>NO ask</dt><dd>{formatContractCents(game.exchange_market?.under_ask_cents)} <small>({game.exchange_market?.under_ask_dollars})</small></dd></div>
        <div><dt>Market phase</dt><dd>{game.exchange_market?.game_phase?.replaceAll('_', ' ')}</dd></div>
      </dl>
      <p class="market-warning">Contract asks are quoted in cents, not American sportsbook odds. Source update age is unknown; fees are excluded.</p>
      {#if game.exchange_market?.failure_reason}<p class="market-warning"><strong>Update failed.</strong> Retaining the captured asks above: {game.exchange_market.failure_reason}</p>{/if}
      <p class="source-line"><span>Kalshi</span><span>Captured/as of {formatTimestamp(game.exchange_market?.observed_at ?? null)}</span><span>{game.exchange_market?.market_ticker}</span></p>
    </section>
  {/if}

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
      <div><dt>Park-factor method</dt><dd>{game.factors.state}</dd></div>
      <div><dt>Flight context</dt><dd>{game.approach_c.state.replaceAll('_', ' ')}</dd></div>
    </dl>
  </section>

  <section class="evidence-section lineup-context" aria-labelledby={`lineup-${game.game_pk}`}>
    <div class="section-heading">
      <div>
        <p class="eyebrow">Lineup state</p>
        <h3 id={`lineup-${game.game_pk}`}>Lineup and flight context</h3>
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
    <p class="plain-note">{game.approach_c.reason ?? game.approach_c.method ?? 'Flight context is optional and never blocks the weather-adjusted park-factor slate.'}</p>
  </section>

  <TrajectoryTheater {game} />
</article>
