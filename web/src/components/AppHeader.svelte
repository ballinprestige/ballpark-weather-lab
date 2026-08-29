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
      <span class="brand-mark" aria-hidden="true"><i></i><i></i><i></i></span>
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
      <span class="theme-icon" aria-hidden="true">{theme === 'day' ? '◐' : '◑'}</span>
      <span class="theme-label">{theme === 'day' ? 'Dark mode' : 'Light mode'}</span>
    </button>
  </div>
</header>
