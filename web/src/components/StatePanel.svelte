<script lang="ts">
  export let eyebrow: string;
  export let title: string;
  export let message: string;
  export let actionLabel: string | null = null;
  export let onAction: (() => void) | null = null;
</script>

<section class="state-panel" aria-labelledby="state-panel-title">
  <p class="eyebrow">{eyebrow}</p>
  <h1 id="state-panel-title">{title}</h1>
  <p>{message}</p>
  {#if actionLabel && onAction}
    <button class="button button-primary" type="button" on:click={onAction}>{actionLabel}</button>
  {/if}
</section>
