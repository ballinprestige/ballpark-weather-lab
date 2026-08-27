<script lang="ts">
  import type { BallparkGame } from '../lib/types';
  import { formatFactor, formatTime, gameHoldReason, isGameHeld, stateTone, teamLabel } from '../lib/format';

  export let game: BallparkGame;
  export let selected = false;
  export let controls: string;
  export let onSelect: () => void;

  $: held = isGameHeld(game);
  $: summaryId = `game-summary-${game.game_pk}`;
</script>

<article class:selected class="game-list-item" data-tone={held ? 'hold' : stateTone(game.factors.state)}>
  <h3>
    <button
      type="button"
      aria-expanded={selected}
      aria-controls={controls}
      aria-describedby={summaryId}
      on:click={onSelect}
    >
      <span class="matchup"><strong>{teamLabel(game.away_team)}</strong><span>at</span><strong>{teamLabel(game.home_team)}</strong></span>
      <span class="game-time">{formatTime(game.game_time)}</span>
    </button>
  </h3>
  <div class="game-list-summary" id={summaryId}>
    <span class="venue-name">{game.venue}</span>
    {#if held}
      <span class="hold-label" title={gameHoldReason(game)}>WEATHER HOLD</span>
    {:else}
      <span class="compact-factor"><small>RUNS PF</small> {formatFactor(game.factors.game_pf_runs)}</span>
    {/if}
  </div>
</article>
