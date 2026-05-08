<script>
	import '../app.css';
</script>

<div class="app">
	<slot />
</div>

<style>
	.app {
		min-height: 100vh;
		background: var(--bg-primary);
	}
</style>
