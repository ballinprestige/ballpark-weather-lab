<script lang="ts">
  import type { BallparkGame, TrajectoryArc, TrajectoryPoint } from '../lib/types';
  import { formatDelta } from '../lib/format';
  import { isReadyState } from '../lib/validate';

  export let game: BallparkGame;
  let selectedIndex = 0;

  $: arcs = game.trajectory.arcs;
  $: if (selectedIndex >= arcs.length) selectedIndex = 0;
  $: selectedArc = arcs[selectedIndex] ?? null;
  $: neutralPath = selectedArc ? pathFor(selectedArc.neutral_points_ft) : '';
  $: weatherPath = selectedArc ? pathFor(selectedArc.weather_points_ft) : '';

  function pathFor(points: TrajectoryPoint[]): string {
    if (!points.length) return '';
    const maxX = Math.max(1, ...points.map(([x]) => x));
    const maxY = Math.max(1, ...points.map(([, y]) => y));
    return points.map(([x, y], index) => {
      const px = 24 + (x / maxX) * 392;
      const py = 210 - (y / maxY) * 174;
      return `${index === 0 ? 'M' : 'L'}${px.toFixed(1)},${py.toFixed(1)}`;
    }).join(' ');
  }

  function arcLabel(arc: TrajectoryArc, index: number): string {
    const parts = [
      typeof arc.launch_angle_deg === 'number' ? `${arc.launch_angle_deg}°` : null,
      typeof arc.exit_velocity_mph === 'number' ? `${arc.exit_velocity_mph} mph` : null
    ].filter(Boolean);
    return arc.archetype || (parts.length ? parts.join(' · ') : `Trajectory ${index + 1}`);
  }
</script>

<section class="evidence-section trajectory" aria-labelledby={`trajectory-${game.game_pk}`}>
  <div class="section-heading">
    <div>
      <p class="eyebrow">Optional physics context</p>
      <h3 id={`trajectory-${game.game_pk}`}>Trajectory theater</h3>
    </div>
    <span class="section-note">Neutral vs weather</span>
  </div>

  {#if isReadyState(game.trajectory.state) && selectedArc}
    <div class="trajectory-tabs" role="tablist" aria-label="Trajectory samples">
      {#each arcs as arc, index}
        <button
          type="button"
          role="tab"
          aria-selected={selectedIndex === index}
          tabindex={selectedIndex === index ? 0 : -1}
          on:click={() => selectedIndex = index}
        >{arcLabel(arc, index)}</button>
      {/each}
    </div>
    <figure class="trajectory-stage">
      <svg viewBox="0 0 440 230" role="img" aria-label={`${arcLabel(selectedArc, selectedIndex)}: neutral and weather-adjusted flight paths`}>
        <path d="M24 210 H416" class="trajectory-ground"></path>
        <path d="M24 35 V210" class="trajectory-axis"></path>
        <text x="24" y="225" class="chart-label">CONTACT</text>
        <text x="416" y="225" text-anchor="end" class="chart-label">CARRY</text>
        <path d={neutralPath} class="trajectory-neutral"></path>
        <path d={weatherPath} class="trajectory-weather"></path>
      </svg>
      <figcaption>
        <span><i class="legend-line neutral" aria-hidden="true"></i> Neutral atmosphere</span>
        <span><i class="legend-line weather" aria-hidden="true"></i> Game-hour weather</span>
        <strong>Carry Δ {formatDelta(selectedArc.carry_delta_ft, 1)} ft</strong>
      </figcaption>
    </figure>
    <p class="plain-note">{game.trajectory.integration ?? 'Lookup-table trajectories provide physical context and do not replace the trained park-factor estimate.'}</p>
  {:else}
    <div class="figure-hold">
      <span class="hold-hatch" aria-hidden="true"></span>
      <p><strong>Trajectory context unavailable.</strong> {game.trajectory.reason ?? 'No validated trajectory lookup was attached to this game.'}</p>
    </div>
  {/if}
</section>
