<script lang="ts">
  import type { BallparkGame, GeometryArtifact } from '../lib/types';
  import { findVenueGeometry, normalizeDegrees, wallPath } from '../lib/geometry';
  import { formatTimestamp, windDirectionDegrees, windLabel, windSpeed } from '../lib/format';
  import { isReadyState } from '../lib/validate';

  export let game: BallparkGame;
  export let geometry: GeometryArtifact | null;

  $: venueGeometry = findVenueGeometry(geometry, game.home_team);
  $: diagramPath = venueGeometry && geometry ? wallPath(geometry.angles_deg, venueGeometry.wall_distance_ft) : '';
  $: direction = windDirectionDegrees(game.weather);
  $: speed = windSpeed(game.weather);
  $: fieldDirection = venueGeometry && direction !== null ? normalizeDegrees(direction - venueGeometry.cf_azimuth + 180) : null;
  $: arrowRadians = fieldDirection === null ? 0 : fieldDirection * Math.PI / 180;
  $: arrowLength = speed === null ? 0 : Math.min(86, 34 + speed * 2.4);
  $: arrowX = 210 + Math.sin(arrowRadians) * arrowLength;
  $: arrowY = 224 - Math.cos(arrowRadians) * arrowLength;
  $: titleId = `park-wind-title-${game.game_pk}`;
  $: descId = `park-wind-desc-${game.game_pk}`;
  $: available = isReadyState(game.weather.state) && venueGeometry !== null;
</script>

<figure class="instrument-figure park-wind" aria-labelledby={titleId} aria-describedby={descId}>
  <div class="figure-heading">
    <div>
      <p class="eyebrow">Field orientation</p>
      <h3 id={titleId}>Park wind diagram</h3>
    </div>
    <span class="figure-reading">{isReadyState(game.weather.state) ? windLabel(game.weather) : 'weather held'}</span>
  </div>

  {#if available && venueGeometry}
    <svg viewBox="0 0 420 286" role="img" aria-labelledby={titleId} aria-describedby={descId}>
      <defs>
        <pattern id="mown-grass" width="18" height="18" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="9" height="18" class="grass-a"></rect>
          <rect x="9" width="9" height="18" class="grass-b"></rect>
        </pattern>
        <marker id="wind-arrowhead" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" class="wind-arrow-head"></path>
        </marker>
      </defs>
      <path d={diagramPath} class="field-fill"></path>
      <path d={diagramPath} class="field-wall"></path>
      <path d="M210 264 L132 186 M210 264 L288 186" class="foul-lines"></path>
      <path d="M210 254 l10 10 -10 10 -10 -10 z" class="infield-mark"></path>
      <circle cx="210" cy="224" r="3.5" class="mound-mark"></circle>
      <text x="53" y="273" class="field-label">LF</text>
      <text x="210" y="37" text-anchor="middle" class="field-label">CF · {venueGeometry.cf_azimuth}°</text>
      <text x="367" y="273" text-anchor="end" class="field-label">RF</text>
      {#if direction !== null && speed !== null}
        <circle cx="210" cy="224" r="11" class="wind-origin"></circle>
        <line x1="210" y1="224" x2={arrowX} y2={arrowY} class="wind-arrow" marker-end="url(#wind-arrowhead)"></line>
        <text x={arrowX} y={arrowY - 10} text-anchor="middle" class="wind-speed">{speed.toFixed(0)} mph</text>
      {:else}
        <text x="210" y="150" text-anchor="middle" class="diagram-hold">Direction not reported</text>
      {/if}
    </svg>
    <figcaption id={descId}>
      The wall trace follows the static {game.venue} geometry. The arrow shows where the reported wind is blowing after rotating its compass bearing into the park’s center-field axis. Weather valid {formatTimestamp(game.weather.valid_at)}.
    </figcaption>
  {:else}
    <div class="figure-hold" id={descId}>
      <span class="hold-hatch" aria-hidden="true"></span>
      <p><strong>Diagram held.</strong> {game.weather.reason ?? (venueGeometry ? 'A usable wind observation is not available.' : 'This venue has no verified geometry in the static artifact.')}</p>
    </div>
  {/if}
</figure>
