<script lang="ts">
  import type { ArchiveEntry, ArchiveIndex } from '../lib/types';
  import { formatDate, formatTimestamp, shortHash } from '../lib/format';
  import StateBadge from './StateBadge.svelte';

  export let archive: ArchiveIndex;
  export let currentDate: string;
  export let selectedDate: string;
  export let loadingDate: string | null = null;
  export let error: string | null = null;
  export let onSelect: (entry: ArchiveEntry) => void;
  export let onReturnLive: () => void;
</script>

<section class="view history-view" aria-labelledby="history-title">
  <header class="view-intro">
    <div>
      <p class="eyebrow">Immutable daily record</p>
      <h1 id="history-title">History</h1>
    </div>
    <p>Each archived day is loaded by date and checked against the hash recorded in the public index.</p>
  </header>

  {#if selectedDate !== currentDate}
    <div class="archive-notice">
      <p><strong>Viewing {formatDate(selectedDate)}.</strong> This is a historical snapshot.</p>
      <button class="button button-secondary" type="button" on:click={onReturnLive}>Return to current release</button>
    </div>
  {/if}

  {#if error}
    <div class="inline-error" role="alert">
      <strong>Archive could not be opened.</strong>
      <span>{error}</span>
    </div>
  {/if}

  {#if archive.dates.length}
    <div class="archive-list" aria-live="polite">
      {#each archive.dates as entry}
        <article class:current={entry.date === currentDate}>
          <div class="archive-date">
            <span>{entry.date === currentDate ? 'CURRENT' : 'ARCHIVE'}</span>
            <h2>{formatDate(entry.date)}</h2>
            <p>{formatTimestamp(entry.generated_at)}</p>
          </div>
          <div class="archive-facts">
            <span>{entry.game_count} {entry.game_count === 1 ? 'game' : 'games'}</span>
            <StateBadge state={entry.status} />
            <code title={entry.payload_sha256}>{shortHash(entry.payload_sha256)}</code>
          </div>
          <button
            class="button button-secondary"
            type="button"
            disabled={loadingDate !== null}
            aria-label={`Open ${formatDate(entry.date)} snapshot`}
            on:click={() => onSelect(entry)}
          >{loadingDate === entry.date ? 'Checking snapshot…' : entry.date === selectedDate ? 'Open snapshot' : 'Inspect snapshot'}</button>
        </article>
      {/each}
    </div>
  {:else}
    <section class="empty-ledger">
      <p class="eyebrow">No archive entries</p>
      <h2>History is not available yet</h2>
      <p>The current slate remains usable. A dated snapshot will appear here after the first successful archived publication.</p>
    </section>
  {/if}
</section>
