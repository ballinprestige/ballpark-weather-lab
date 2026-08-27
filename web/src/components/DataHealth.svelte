<script lang="ts">
  import type { BallparkPayload, GeometryArtifact } from '../lib/types';
  import { formatTimestamp, healthReason, healthState, humanizeKey, shortHash } from '../lib/format';
  import { isReadyState } from '../lib/validate';
  import StateBadge from './StateBadge.svelte';

  export let payload: BallparkPayload;
  export let payloadHash: string;
  export let geometry: GeometryArtifact | null;
  export let warnings: string[] = [];
  export let isArchive = false;

  $: lanes = Object.entries(payload.health) as Array<[keyof BallparkPayload['health'], BallparkPayload['health'][keyof BallparkPayload['health']]]>;
  $: weatherReady = payload.games.filter((game) => isReadyState(game.weather.state)).length;
  $: factorReady = payload.games.filter((game) => isReadyState(game.factors.state)).length;
  $: lineupsReady = payload.games.filter((game) => isReadyState(game.lineup.state)).length;
  $: trajectoryReady = payload.games.filter((game) => isReadyState(game.trajectory.state)).length;
</script>

<section class="view health-view" aria-labelledby="health-title">
  <header class="view-intro">
    <div>
      <p class="eyebrow">Publication ledger</p>
      <h1 id="health-title">Data Health</h1>
    </div>
    <p>What arrived, what was held, and exactly which artifact this page read.</p>
  </header>

  <section class="release-receipt" aria-labelledby="receipt-title">
    <div>
      <p class="eyebrow">Public readback</p>
      <h2 id="receipt-title">{isArchive ? 'Archived snapshot' : 'Current release'}</h2>
    </div>
    <dl>
      <div><dt>Data date</dt><dd>{payload.date}</dd></div>
      <div><dt>Generated</dt><dd>{formatTimestamp(payload.generated_at)}</dd></div>
      <div><dt>Payload SHA-256</dt><dd><code title={payloadHash}>{shortHash(payloadHash)}</code></dd></div>
      <div><dt>Schema</dt><dd>{payload.schema_version}</dd></div>
      <div><dt>Publication state</dt><dd><StateBadge state={payload.status} /></dd></div>
      <div><dt>Geometry artifact</dt><dd>{geometry ? `${Object.keys(geometry.venues).length} parks` : 'Unavailable'}</dd></div>
    </dl>
  </section>

  <section class="health-lanes" aria-labelledby="lanes-title">
    <div class="section-heading">
      <div>
        <p class="eyebrow">Dependency state</p>
        <h2 id="lanes-title">Four publication lanes</h2>
      </div>
      <span class="section-note">Optional never blocks core</span>
    </div>
    <div class="health-lane-list">
      {#each lanes as [name, lane], index}
        <article>
          <div class="lane-index" aria-hidden="true">{String(index + 1).padStart(2, '0')}</div>
          <div>
            <h3>{humanizeKey(name)}</h3>
            <p>{healthReason(lane) ?? (isReadyState(healthState(lane)) ? 'Validated for this publication.' : 'This lane did not report additional detail.')}</p>
          </div>
          <StateBadge state={healthState(lane)} />
        </article>
      {/each}
    </div>
  </section>

  <section class="coverage-ledger" aria-labelledby="coverage-title">
    <div class="section-heading">
      <div>
        <p class="eyebrow">Slate coverage</p>
        <h2 id="coverage-title">Per-game evidence</h2>
      </div>
      <span class="section-note">of {payload.games.length} games</span>
    </div>
    <dl>
      <div><dt>Weather ready</dt><dd>{weatherReady}<span>/{payload.games.length}</span></dd></div>
      <div><dt>Factors ready</dt><dd>{factorReady}<span>/{payload.games.length}</span></dd></div>
      <div><dt>Lineups confirmed</dt><dd>{lineupsReady}<span>/{payload.games.length}</span></dd></div>
      <div><dt>Trajectory context</dt><dd>{trajectoryReady}<span>/{payload.games.length}</span></dd></div>
    </dl>
    <p class="plain-note">Schedule and validated weather/factors define the core slate. Lineups and trajectory context may arrive later without suppressing ready games.</p>
  </section>

  {#if warnings.length}
    <section class="warning-ledger" aria-labelledby="warnings-title">
      <p class="eyebrow">Non-blocking notes</p>
      <h2 id="warnings-title">Degraded enhancements</h2>
      <ul>
        {#each warnings as warning}<li>{warning}</li>{/each}
      </ul>
    </section>
  {/if}
</section>
