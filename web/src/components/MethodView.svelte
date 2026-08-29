<script lang="ts">
  import type { BallparkPayload, JsonRecord } from '../lib/types';
  import { displayValue, humanizeKey } from '../lib/format';

  export let payload: BallparkPayload;

  const preferredOrder = [
    'evidence_games', 'split', 'held_out_rmse', 'artifact_version', 'name', 'statement'
  ];
  const excluded = new Set(['limitations', 'approach_b', 'approach_c', 'description']);

  $: modelEntries = Object.entries(payload.model)
    .filter(([key]) => !excluded.has(key))
    .sort(([a], [b]) => {
      const ai = preferredOrder.indexOf(a);
      const bi = preferredOrder.indexOf(b);
      return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi) || a.localeCompare(b);
    });
  $: limitations = limitationList(payload.model.limitations);

  function limitationList(value: unknown): string[] {
    if (Array.isArray(value)) return value.map(displayValue);
    if (value && typeof value === 'object') return Object.entries(value as JsonRecord).map(([key, item]) => `${humanizeKey(key)}: ${displayValue(item)}`);
    if (typeof value === 'string' && value.trim()) return [value];
    return [
      'Game-hour weather can change after publication; valid and fetched timestamps remain visible with every game.',
      'Park factors describe environmental context around a neutral value of 1.000. They are not outcome or score forecasts.',
      'Lineup and trajectory physics are optional context. Missing optional inputs do not invalidate an otherwise ready weather slate.'
    ];
  }

  function modelValue(key: string, value: unknown): string {
    if (key === 'split' && value && typeof value === 'object') {
      const split = value as JsonRecord;
      return `Fit: ${displayValue(split.train)} · 2024 validation: ${displayValue(split.validation_2024)} · 2025 held-out test: ${displayValue(split.test_2025)}`;
    }
    if (key === 'held_out_rmse' && value && typeof value === 'object') {
      const rmse = value as JsonRecord;
      return `Runs: ${displayValue(rmse.runs)} · Home runs: ${displayValue(rmse.home_runs)}`;
    }
    return displayValue(value);
  }
</script>

<section class="view method-view" aria-labelledby="method-title">
  <header class="view-intro method-intro">
    <div>
      <p class="eyebrow">Transparent by construction</p>
      <h1 id="method-title">Method &amp; model evidence</h1>
    </div>
    <p>One validated payload carries the slate, its contributing observations, model identity, optional physics context, and public digest.</p>
  </header>

  <section class="critical-path" aria-labelledby="path-title">
    <div class="section-heading">
      <div>
        <p class="eyebrow">Publication design</p>
        <h2 id="path-title">The five-step critical path</h2>
      </div>
      <span class="section-note">One command · one payload</span>
    </div>
    <ol>
      <li><span>01</span><div><strong>Schedule + game-hour weather</strong><p>Resolve the day’s MLB games, then attach time-aligned weather with source and observation receipts.</p></div></li>
      <li><span>02</span><div><strong>Approach B park factors</strong><p>Combine each venue baseline with the trained weather response for runs and home runs.</p></div></li>
      <li><span>03</span><div><strong>Optional Approach C</strong><p>Add lineup and trajectory physics only when their evidence is ready. Missing optional inputs never block the core slate.</p></div></li>
      <li><span>04</span><div><strong>Schema validation</strong><p>Reject malformed envelopes and duplicate game IDs; hold incomplete games without hiding valid neighbors.</p></div></li>
      <li><span>05</span><div><strong>Atomic public readback</strong><p>Publish the payload and release pointer, then verify date and SHA-256 before rendering.</p></div></li>
    </ol>
  </section>

  <section class="model-evidence" aria-labelledby="evidence-title">
    <div class="section-heading">
      <div>
        <p class="eyebrow">Payload manifest</p>
        <h2 id="evidence-title">Defensible model evidence</h2>
      </div>
      <span class="section-note">Reported, not inferred</span>
    </div>
    {#if modelEntries.length}
      <dl>
        {#each modelEntries as [key, value]}
          <div>
            <dt>{humanizeKey(key)}</dt>
            <dd>{modelValue(key, value)}</dd>
          </div>
        {/each}
      </dl>
    {:else}
      <p class="plain-note">This payload does not expose model-manifest fields. No evidence counts are inferred by the interface.</p>
    {/if}
  </section>

  <section class="limitations" aria-labelledby="limitations-title">
    <p class="eyebrow">Boundaries</p>
    <h2 id="limitations-title">What this application does not claim</h2>
    <ul>
      {#each limitations as limitation}<li>{limitation}</li>{/each}
    </ul>
  </section>
</section>
