<script lang="ts">
  import { humanizeKey, stateTone } from '../lib/format';

  export let state: string;
  export let label: string | null = null;

  $: tone = stateTone(state);
  $: text = label ?? humanizeKey(state);
</script>

<span class="state-badge" data-tone={tone}>
  <span class="state-mark" aria-hidden="true"></span>
  {text}
</span>
