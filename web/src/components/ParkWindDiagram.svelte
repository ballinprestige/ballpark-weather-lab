<script lang="ts">
  import type { BallparkGame, GeometryArtifact } from '../lib/types';
  import { distanceAtAngle, findVenueGeometry, wallPath } from '../lib/geometry';
  import { formatTimestamp, windLabel } from '../lib/format';
  import { isReadyState } from '../lib/validate';
  import { isOutdoorWindSuppressed, parkWindVector } from '../lib/wind';

  export let game: BallparkGame;
  export let geometry: GeometryArtifact | null;

  $: venueGeometry = findVenueGeometry(geometry, game.home_team);
  $: diagramPath = venueGeometry && geometry ? wallPath(geometry.angles_deg, venueGeometry.wall_distance_ft) : '';
  $: vector = venueGeometry ? parkWindVector(game.weather, venueGeometry.cf_azimuth) : null;
  // Geometry drives the drawing; published components drive the displayed
  // value so float recomputation cannot disagree with the slate board.
  $: displayCarry = game.weather.wind_carry_mph;
  $: displayCross = game.weather.wind_cross_mph;
  $: weatherReady = isReadyState(game.weather.state);
  $: roofSuppressed = isOutdoorWindSuppressed(game.weather);
  $: unknownRoof = game.weather.roof_state === 'unknown' || game.weather.roof_state === 'unconfirmed';
  $: canDraw = weatherReady && venueGeometry !== null;
  $: canStream = canDraw && vector !== null && !roofSuppressed && game.weather.wind_speed_mph > 0;
  $: calm = canDraw && vector !== null && game.weather.wind_speed_mph === 0;
  $: titleId = `park-wind-title-${game.game_pk}`;
  $: descId = `park-wind-desc-${game.game_pk}`;
  $: markerId = `wind-arrowhead-${game.game_pk}`;
  $: gridId = `park-grid-${game.game_pk}`;
  $: streamPeriod = vector ? `${Math.max(0.8, 4.8 - Math.min(3.6, game.weather.wind_speed_mph * 0.16)).toFixed(2)}s` : '0s';
  $: lf = venueGeometry && geometry ? distanceAtAngle(geometry.angles_deg, venueGeometry.wall_distance_ft, -45) : null;
  $: cf = venueGeometry && geometry ? distanceAtAngle(geometry.angles_deg, venueGeometry.wall_distance_ft, 0) : null;
  $: rf = venueGeometry && geometry ? distanceAtAngle(geometry.angles_deg, venueGeometry.wall_distance_ft, 45) : null;

  const x = (angle: number, distance: number) => 210 + Math.sin(angle * Math.PI / 180) * distance * 0.55;
  const y = (angle: number, distance: number) => 264 - Math.cos(angle * Math.PI / 180) * distance * 0.55;
  const signed = (value: number) => `${value >= 0 ? '+' : '−'}${Math.abs(value).toFixed(1)}`;
  const streamPath = (offset: number) => {
    if (!vector) return '';
    const px = -vector.svgY * offset;
    const py = vector.svgX * offset;
    const startX = 210 - vector.svgX * 158 + px;
    const startY = 178 - vector.svgY * 158 + py;
    const endX = 210 + vector.svgX * 162 + px;
    const endY = 178 + vector.svgY * 162 + py;
    return `M ${startX.toFixed(1)} ${startY.toFixed(1)} L ${endX.toFixed(1)} ${endY.toFixed(1)}`;
  };
</script>

<figure class="instrument-figure park-wind" aria-labelledby={titleId} aria-describedby={descId} style={`--wind-period:${streamPeriod}`}>
  <div class="figure-heading park-heading">
    <div><p class="eyebrow">Park wind diagram</p><h3 id={titleId}>{game.venue}</h3></div>
    <div class="carry-reading" aria-label={vector ? `Carry ${signed(displayCarry)} miles per hour` : 'Wind direction unavailable'}>
      <span>CARRY</span><strong>{vector && !roofSuppressed ? `${signed(displayCarry)} mph` : roofSuppressed ? 'ROOF' : '—'}</strong>
    </div>
  </div>

  {#if canDraw && venueGeometry}
    <div class="park-stage">
      <svg viewBox="0 0 420 286" role="img" aria-labelledby={titleId} aria-describedby={descId}>
        <defs>
          <pattern id={gridId} width="18" height="18" patternUnits="userSpaceOnUse"><path d="M 18 0 L 0 0 0 18" class="park-grid-line"></path></pattern>
          <marker id={markerId} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" class="wind-arrow-head"></path></marker>
        </defs>
        <rect x="18" y="18" width="384" height="248" class="park-grid" fill={`url(#${gridId})`}></rect>
        {#each [250, 300, 350, 400] as range}<path d={`M ${x(-45, range)} ${y(-45, range)} Q 210 ${y(0, range)} ${x(45, range)} ${y(45, range)}`} class="range-arc"></path>{/each}
        <path d={diagramPath} class="field-fill"></path><path d={diagramPath} class="field-wall"></path>
        <path d="M210 264 L132 186 M210 264 L288 186" class="foul-lines"></path><path d="M210 254 l10 10 -10 10 -10 -10 z" class="infield-mark"></path><circle cx="210" cy="224" r="3.5" class="mound-mark"></circle>
        {#if canStream}<g class="wind-streams" aria-hidden="true">{#each [-27, -9, 9, 27] as offset, index}<path d={streamPath(offset)} class="wind-stream" style={`animation-delay:-${(index * 0.44).toFixed(2)}s`}></path>{/each}</g>{/if}
        <text x="45" y="265" class="field-label">LF {lf ? Math.round(lf) : '—'}</text><text x="210" y="35" text-anchor="middle" class="field-label">CF {cf ? Math.round(cf) : '—'}</text><text x="375" y="265" text-anchor="end" class="field-label">RF {rf ? Math.round(rf) : '—'}</text>
        <g class="north-compass" transform="translate(355 65)"><circle r="23"></circle><path d="M 0 17 V -17" marker-end={`url(#${markerId})`}></path><text y="-29" text-anchor="middle">N</text></g>
        {#if vector && !roofSuppressed}<line x1="210" y1="224" x2={210 + vector.svgX * 48} y2={224 + vector.svgY * 48} class="wind-vector" marker-end={`url(#${markerId})`}></line>{/if}
        {#if roofSuppressed}<text x="210" y="155" text-anchor="middle" class="diagram-hold">Roof active · outdoor wind withheld</text>
        {:else if unknownRoof}<text x="210" y="155" text-anchor="middle" class="diagram-note">Roof status unconfirmed · outdoor scenario</text>
        {:else if !vector}<text x="210" y="155" text-anchor="middle" class="diagram-hold">Direction not reported · no vector shown</text>
        {:else if calm}<text x="210" y="155" text-anchor="middle" class="diagram-note">Calm · 0 mph</text>{/if}
      </svg>
      <dl class="wind-decomposition" aria-label="Park-relative wind decomposition"><div><dt>FROM</dt><dd>{vector ? `${Math.round(vector.fromDeg)}°` : '—'}</dd></div><div><dt>CROSS</dt><dd>{vector && !roofSuppressed ? `${signed(displayCross)} mph` : '—'}</dd></div><div><dt>CF AXIS</dt><dd>{venueGeometry.cf_azimuth}°</dd></div></dl>
    </div>
    <figcaption id={descId}>Reported wind is meteorological <strong>FROM</strong>; streams show physical <strong>TO</strong>, rotated against this venue’s centre-field axis. {roofSuppressed ? 'Outdoor effect is suppressed while the roof is active.' : `Weather valid ${formatTimestamp(game.weather.valid_at)}.`}</figcaption>
  {:else}
    <div class="figure-hold" id={descId}><span class="hold-hatch" aria-hidden="true"></span><p><strong>Park wind diagram held.</strong> {game.weather.reason ?? (venueGeometry ? 'A usable wind observation is not available.' : 'This venue has no verified geometry in the static artifact.')}</p></div>
  {/if}
  <p class="wind-source-line">{weatherReady ? windLabel(game.weather) : 'Weather held'} · {game.weather.source ?? 'Source not reported'}</p>
</figure>
