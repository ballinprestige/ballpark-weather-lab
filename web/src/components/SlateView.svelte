<script lang="ts">
  import { onMount } from 'svelte';
  import type { BallparkPayload, GeometryArtifact } from '../lib/types';
  import { formatDate } from '../lib/format';
  import GameDetail from './GameDetail.svelte';
  import GameListItem from './GameListItem.svelte';

  export let payload: BallparkPayload;
  export let geometry: GeometryArtifact | null;

  let selectedKey = payload.games[0] ? String(payload.games[0].game_pk) : '';
  let desktop = false;

  $: if (payload.games.length && !payload.games.some((game) => String(game.game_pk) === selectedKey)) {
    selectedKey = String(payload.games[0].game_pk);
  }
  $: selectedGame = payload.games.find((game) => String(game.game_pk) === selectedKey) ?? payload.games[0];

  onMount(() => {
    const media = window.matchMedia('(min-width: 62rem)');
    const update = () => desktop = media.matches;
    update();
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  });

  function selectGame(key: string): void {
    selectedKey = selectedKey === key && !desktop ? '' : key;
  }
</script>

<section class="view slate-view" aria-labelledby="slate-title">
  <header class="view-intro slate-intro">
    <div>
      <p class="eyebrow">Daily field sheet · {payload.games.length} {payload.games.length === 1 ? 'game' : 'games'}</p>
      <h1 id="slate-title">{formatDate(payload.date)} slate</h1>
    </div>
    <p>Venue baselines meet game-hour weather. Select a matchup to inspect every contribution.</p>
  </header>

  <div class="slate-workbench">
    <section class="slate-master" aria-labelledby="game-list-title">
      <div class="master-heading">
        <h2 id="game-list-title">Matchups</h2>
        <span>PF neutral 1.000</span>
      </div>
      <div class="game-list">
        {#each payload.games as game (game.game_pk)}
          {@const key = String(game.game_pk)}
          {@const expanded = key === selectedKey}
          <GameListItem
            {game}
            selected={expanded}
            controls={desktop ? 'desktop-game-detail' : `mobile-game-detail-${game.game_pk}`}
            onSelect={() => selectGame(key)}
          />
          {#if !desktop && expanded}
            <div class="mobile-game-detail" id={`mobile-game-detail-${game.game_pk}`}>
              <GameDetail {game} {geometry} headingLevel={3} />
            </div>
          {/if}
        {/each}
      </div>
    </section>

    {#if desktop && selectedGame}
      <div class="slate-detail" id="desktop-game-detail">
        {#key selectedGame.game_pk}
          <GameDetail game={selectedGame} {geometry} />
        {/key}
      </div>
    {/if}
  </div>
</section>
