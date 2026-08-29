<script lang="ts">
  import { onMount } from 'svelte';
  import type { BallparkGame, BallparkPayload, GeometryArtifact } from '../lib/types';
  import { formatDate, formatDelta, formatFactor, formatTime, isGameHeld, stateTone, teamLabel } from '../lib/format';
  import GameListItem from './GameListItem.svelte';
  import WindFieldStrip from './WindFieldStrip.svelte';

  type SlateFilter = 'all' | 'open' | 'roof' | 'incomplete';
  type SlateSort = 'movement' | 'time' | 'wind' | 'venue';

  export let payload: BallparkPayload;
  export let geometry: GeometryArtifact | null;
  export let onOpenGame: (key: string) => void;

  let desktop = false;
  let filter: SlateFilter = 'all';
  let sort: SlateSort = 'movement';
  let ledger = false;

  const movementPercent = (game: BallparkGame): number | null => {
    if (isGameHeld(game)) return null;
    return (game.factors.weather_multiplier_runs - 1) * 100;
  };

  const filtered = (game: BallparkGame, activeFilter: SlateFilter): boolean => {
    if (activeFilter === 'open') return game.weather.roof_state === 'open-air' && !isGameHeld(game);
    if (activeFilter === 'roof') return game.weather.dome_active || game.weather.roof_state === 'fixed-roof';
    if (activeFilter === 'incomplete') return isGameHeld(game);
    return true;
  };

  const sorted = (games: BallparkGame[], activeSort: SlateSort): BallparkGame[] => [...games].sort((left, right) => {
    const leftHeld = isGameHeld(left);
    const rightHeld = isGameHeld(right);
    if (leftHeld !== rightHeld) return leftHeld ? 1 : -1;
    if (activeSort === 'time') return Date.parse(left.game_time) - Date.parse(right.game_time);
    if (activeSort === 'wind') return Math.abs(right.weather.wind_carry_mph) - Math.abs(left.weather.wind_carry_mph);
    if (activeSort === 'venue') return left.venue.localeCompare(right.venue);
    const leftMovement = movementPercent(left);
    const rightMovement = movementPercent(right);
    if (leftMovement === null) return rightMovement === null ? 0 : 1;
    if (rightMovement === null) return -1;
    return Math.abs(rightMovement) - Math.abs(leftMovement);
  });

  const temperatureRange = (games: BallparkGame[]): string => {
    const values = games
      .filter((game) => !game.weather.dome_active && !isGameHeld(game))
      .map((game) => game.weather.temperature_f)
      .filter(Number.isFinite);
    return values.length ? `${Math.round(Math.min(...values))}–${Math.round(Math.max(...values))}°F` : 'temperature held';
  };

  const firstPitchRange = (games: BallparkGame[]): string => {
    const values = games
      .map((game) => game.game_time)
      .filter((value) => Number.isFinite(Date.parse(value)))
      .sort((left, right) => Date.parse(left) - Date.parse(right));
    if (!values.length) return 'time not reported';
    if (values.length === 1) return formatTime(values[0]);
    return `${formatTime(values[0])}–${formatTime(values[values.length - 1])}`;
  };

  const statusLabel = (game: BallparkGame): string => {
    if (isGameHeld(game)) return 'INCOMPLETE';
    if (game.weather.dome_active) return 'ROOF';
    return game.weather.basis === 'observation' ? 'OBSERVED' : 'VERIFIED';
  };

  onMount(() => {
    const media = window.matchMedia('(min-width: 66rem)');
    const update = () => desktop = media.matches;
    update();
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  });

  $: games = sorted(payload.games.filter((game) => filtered(game, filter)), sort);
  $: lift = payload.games.filter((game) => (movementPercent(game) ?? 0) >= 3).length;
  $: drag = payload.games.filter((game) => (movementPercent(game) ?? 0) <= -3).length;
  $: verified = payload.games.filter((game) => !isGameHeld(game)).length;
  $: confirmed = payload.games.filter((game) => game.lineup.state === 'confirmed').length;
</script>

<section class="view slate-view" aria-labelledby="slate-title">
  <section class="heritage-slate-intro">
    <div>
      <p class="eyebrow">Station report · {formatDate(payload.date)} · {payload.games.length} {payload.games.length === 1 ? 'game' : 'games'}</p>
      <h1 id="slate-title" aria-label={`${formatDate(payload.date)} slate — The Slate`}>The Slate</h1>
      <p class="lede">Today’s air against each park’s own baseline. The movement—not the reputation—is the signal.</p>
    </div>
    <div class="slate-station">
      <dl class="slate-counts" aria-label="Slate summary">
        <div><dt>games</dt><dd>{payload.games.length}</dd></div>
        <div><dt>lift</dt><dd>{lift}</dd></div>
        <div><dt>drag</dt><dd>{drag}</dd></div>
      </dl>
      <div class="slate-synopsis">
        <div><span>Slate conditions</span><strong>{firstPitchRange(payload.games)} · {temperatureRange(payload.games)}</strong></div>
        <div><span>Instrument</span><strong>{verified}/{payload.games.length} weather · {confirmed}/{payload.games.length} lineups</strong></div>
      </div>
    </div>
  </section>

  <section class="slate-controls" aria-label="Slate controls">
    <div class="control-group">
      <span class="control-label">Filter</span>
      <div class="pill-row" role="group" aria-label="Filter games">
        {#each [['all', 'All'], ['open', 'Open air'], ['roof', 'Roof'], ['incomplete', 'Incomplete']] as option}
          <button class:active={filter === option[0]} aria-pressed={filter === option[0]} type="button" on:click={() => filter = option[0] as SlateFilter}>{option[1]}</button>
        {/each}
      </div>
    </div>
    <label class="sort-control">
      <span class="control-label">Sort</span>
      <select bind:value={sort}>
        <option value="movement">Largest movement</option>
        <option value="time">First pitch</option>
        <option value="wind">Carry wind</option>
        <option value="venue">Venue</option>
      </select>
    </label>
    <button class="view-toggle" class:active={ledger} type="button" on:click={() => ledger = !ledger} aria-pressed={ledger}>
      <span aria-hidden="true">{ledger ? '▦' : '☷'}</span>{ledger ? 'Card mode' : 'Ledger mode'}
    </button>
  </section>

  <h2 class="visually-hidden" id="games-heading">Games</h2>

  {#if games.length === 0}
    <div class="slate-empty">
      <strong>No games match this filter.</strong>
      <button type="button" on:click={() => filter = 'all'}>Show the full slate</button>
    </div>
  {:else if desktop && ledger}
    <div class="ledger-wrap">
      <table class="ledger">
        <thead>
          <tr><th>Game</th><th>Time</th><th>Venue</th><th class="num">Factor Δ</th><th class="num">Run PF</th><th class="num">HR PF</th><th class="num">Carry</th><th class="num">Cross</th><th class="num">Temp</th><th class="num">Air</th><th>State</th><th>Action</th></tr>
        </thead>
        <tbody>
          {#each games as game (game.game_pk)}
            {@const key = String(game.game_pk)}
            {@const movement = movementPercent(game)}
            <tr data-tone={stateTone(game.factors.state)}>
              <td><strong>{game.away_team} <i>at</i> {game.home_team}</strong></td>
              <td>{formatTime(game.game_time)}</td>
              <td>{game.venue}</td>
              <td class="num movement-cell">{movement == null ? '—' : formatDelta(movement, 0) + '%'}</td>
              <td class="num">{isGameHeld(game) ? '—' : formatFactor(game.factors.game_pf_runs)}</td>
              <td class="num">{isGameHeld(game) ? '—' : formatFactor(game.factors.game_pf_hr)}</td>
              <td class="num wind-derived">{isGameHeld(game) ? '—' : formatDelta(game.weather.wind_carry_mph, 1)}</td>
              <td class="num wind-derived">{isGameHeld(game) ? '—' : formatDelta(game.weather.wind_cross_mph, 1)}</td>
              <td class="num">{isGameHeld(game) ? '—' : `${Math.round(game.weather.temperature_f)}°`}</td>
              <td class="num">{isGameHeld(game) ? '—' : game.weather.air_density_index.toFixed(1)}</td>
              <td><span class="ledger-state" data-tone={isGameHeld(game) ? 'hold' : 'good'}>{statusLabel(game)}</span></td>
              <td><button class="inspect-button" data-game-key={game.game_pk} type="button" aria-label={`Open ${teamLabel(game.away_team)} at ${teamLabel(game.home_team)} station`} on:click={() => onOpenGame(key)}>Inspect</button></td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {:else}
    <div class="heritage-game-grid">
      {#each games as game, index (game.game_pk)}
        {@const key = String(game.game_pk)}
        <div class="heritage-game-cell">
          <GameListItem
            {game}
            {geometry}
            rank={sort === 'movement' && index < 3 ? index + 1 : null}
            onOpen={() => onOpenGame(key)}
          />
        </div>
      {/each}
    </div>
  {/if}

  {#if games.length}
    <WindFieldStrip {games} onOpen={onOpenGame} />
  {/if}
</section>
