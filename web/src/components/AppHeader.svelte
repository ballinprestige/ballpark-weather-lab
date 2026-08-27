<script context="module" lang="ts">
  export type ViewName = 'slate' | 'health' | 'history' | 'method';
</script>

<script lang="ts">
  export let current: ViewName;
  export let theme: 'day' | 'night';
  export let onThemeToggle: () => void;

  const links: Array<{ id: ViewName; label: string }> = [
    { id: 'slate', label: 'Slate' },
    { id: 'health', label: 'Data Health' },
    { id: 'history', label: 'History' },
    { id: 'method', label: 'Method' }
  ];
</script>

<header class="site-header">
  <div class="header-inner">
    <a class="brand" href="#slate" aria-label="Ballpark Weather Lab, Slate">
      <svg viewBox="0 0 46 46" aria-hidden="true">
        <path d="M7 39V17L23 6l16 11v22" class="brand-field"></path>
        <path d="M11 35l12-9 12 9M23 26V10" class="brand-seams"></path>
        <circle cx="23" cy="26" r="3" class="brand-mound"></circle>
      </svg>
      <span>
        <strong>Ballpark</strong>
        <small>Weather Lab</small>
      </span>
    </a>

    <nav aria-label="Primary">
      {#each links as link}
        <a href={`#${link.id}`} aria-current={current === link.id ? 'page' : undefined}>{link.label}</a>
      {/each}
    </nav>

    <button class="theme-toggle" type="button" aria-label={`Use ${theme === 'day' ? 'night' : 'day'} theme`} on:click={onThemeToggle}>
      <span aria-hidden="true">{theme === 'day' ? '◐' : '◑'}</span>
      <span class="theme-label">{theme === 'day' ? 'Night' : 'Day'}</span>
    </button>
  </div>
</header>
