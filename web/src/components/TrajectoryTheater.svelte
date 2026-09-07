<script lang="ts">
  import type { BallparkGame, TrajectoryArc, TrajectoryPoint } from '../lib/types';
  import { formatDelta } from '../lib/format';
  import { isReadyState } from '../lib/validate';

  export let game: BallparkGame;
  let selectedIndex = 0;
  let tabButtons: HTMLButtonElement[] = [];

  $: arcs = game.trajectory.arcs;
  $: if (selectedIndex >= arcs.length) selectedIndex = 0;
  $: selectedArc = arcs[selectedIndex] ?? null;
  $: plotX = selectedArc ? Math.max(1, ...selectedArc.neutral_points_ft.map(([x]) => x), ...selectedArc.weather_points_ft.map(([x]) => x)) : 1;
  $: plotY = selectedArc ? Math.max(1, ...selectedArc.neutral_points_ft.map(([, y]) => y), ...selectedArc.weather_points_ft.map(([, y]) => y)) : 1;
  $: neutralPath = selectedArc ? pathFor(selectedArc.neutral_points_ft, plotX, plotY) : '';
  $: weatherPath = selectedArc ? pathFor(selectedArc.weather_points_ft, plotX, plotY) : '';

  function pathFor(points: TrajectoryPoint[], maxX: number, maxY: number): string {
    if (!points.length) return '';
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

  function selectArc(index: number, focus = false): void {
    selectedIndex = index;
    if (focus) requestAnimationFrame(() => tabButtons[index]?.focus());
  }

  function handleTabKey(event: KeyboardEvent, index: number): void {
    if (!arcs.length) return;
    let next: number | null = null;
    if (event.key === 'ArrowRight') next = (index + 1) % arcs.length;
    if (event.key === 'ArrowLeft') next = (index - 1 + arcs.length) % arcs.length;
    if (event.key === 'Home') next = 0;
    if (event.key === 'End') next = arcs.length - 1;
    if (next === null) return;
    event.preventDefault();
    selectArc(next, true);
  }
</script>

<section class="evidence-section trajectory" aria-labelledby={`trajectory-${game.game_pk}`}>
  <div class="section-heading">
    <div>
      <p class="eyebrow">Optional physics context</p>
      <h3 id={`trajectory-${game.game_pk}`}>Flight-path comparison</h3>
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
          aria-controls={`trajectory-panel-${game.game_pk}`}
          tabindex={selectedIndex === index ? 0 : -1}
          bind:this={tabButtons[index]}
          on:click={() => selectArc(index)}
          on:keydown={(event) => handleTabKey(event, index)}
        >{arcLabel(arc, index)}</button>
      {/each}
    </div>
    <div class="trajectory-stage" id={`trajectory-panel-${game.game_pk}`} role="tabpanel" aria-label={`${arcLabel(selectedArc, selectedIndex)} flight-path plot`}>
      <figure>
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
    </div>
    <p class="plain-note">{game.trajectory.integration ?? 'Lookup-table trajectories provide physical context and do not replace the trained park-factor estimate.'}</p>
  {:else}
    <div class="figure-hold">
      <span class="hold-hatch" aria-hidden="true"></span>
      <p><strong>Trajectory context unavailable.</strong> {game.trajectory.reason ?? 'No validated trajectory lookup was attached to this game.'}</p>
    </div>
  {/if}
</section>
