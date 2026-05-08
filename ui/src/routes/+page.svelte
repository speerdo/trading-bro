<script lang="ts">
	import { onMount } from 'svelte';
	import { apiStatus, apiTrades, apiSignals, apiStats, apiWatchlist, apiPositions, apiRunScreener, apiClosePosition, apiResetCircuit, apiUpdateConfig } from '$lib/api';
	import type { Status, Trade, Signal, Position } from '$lib/types';

	let status: Status | null = $state(null);
	let trades: Trade[] = $state([]);
	let signals: Signal[] = $state([]);
	let positions: Position[] = $state([]);
	let watchlist: string[] = $state([]);
	let stats = $state({ closed_count: 0, wins: 0, losses: 0, pnl_today: 0, win_rate: 0 });
	let error = $state('');
	let activeTab = $state('positions');

	async function loadAll() {
		try {
			[status, trades, signals, stats, watchlistData, positions] = await Promise.all([
				apiStatus(),
				apiTrades(),
				apiSignals(),
				apiStats(),
				apiWatchlist(),
				apiPositions(),
			]);
			watchlist = watchlistData.coins || [];
			error = '';
		} catch (e: any) {
			error = e.message;
		}
	}

	onMount(() => {
		loadAll();
		const interval = setInterval(loadAll, 10000);
		return () => clearInterval(interval);
	});

	async function runScreener() {
		try {
			await apiRunScreener();
			await loadAll();
		} catch (e: any) {
			error = e.message;
		}
	}

	async function closePosition(symbol: string) {
		try {
			await apiClosePosition(symbol);
			await loadAll();
		} catch (e: any) {
			error = e.message;
		}
	}

	async function resetCircuit() {
		try {
			await apiResetCircuit();
			await loadAll();
		} catch (e: any) {
			error = e.message;
		}
	}

	async function updateConfig(key: string, value: string) {
		try {
			await apiUpdateConfig(key, value);
			await loadAll();
		} catch (e: any) {
			error = e.message;
		}
	}

	function formatPnL(val: number) {
		const sign = val >= 0 ? '+' : '';
		return `${sign}$${val.toFixed(2)}`;
	}
</script>

<div class="dashboard">
	<!-- Header -->
	<header class="header">
		<div class="header-left">
			<h1>TradeBrain</h1>
			<span class="version">Burt</span>
		</div>
		<div class="header-center">
			{#if status}
				<span class="badge {status.paper_trading ? 'badge-green' : 'badge-red'}">
					{status.paper_trading ? 'PAPER' : 'LIVE'}
				</span>
				<span class="badge {status.circuit_breaker_active ? 'badge-red' : 'badge-green'}">
					{status.circuit_breaker_active ? 'CIRCUIT BREAKER' : 'ACTIVE'}
				</span>
				<span class="badge badge-orange">{status.strategy}</span>
			{/if}
		</div>
		<div class="header-right">
			{#if status}
				<div class="stat">
					<span class="stat-label">Today</span>
					<span class="stat-value {stats.pnl_today >= 0 ? 'green' : 'red'}">
						{formatPnL(stats.pnl_today)}
					</span>
				</div>
			{/if}
		</div>
	</header>

	{#if error}
		<div class="error-banner">{error}</div>
	{/if}

	<div class="main-grid">
		<!-- Sidebar -->
		<aside class="sidebar">
			<div class="card">
				<h3>Controls</h3>
				{#if status}
					<div class="control-group">
						<label>Strategy</label>
						<select value={status.strategy} onchange={(e) => updateConfig('strategy', e.currentTarget.value)}>
							<option value="rsi_macd">RSI + MACD</option>
							<option value="bollinger">Bollinger</option>
							<option value="ema_pullback">EMA Pullback</option>
						</select>
					</div>
					<div class="control-group">
						<label>Leverage: {status.leverage}x</label>
						<input type="range" min="1" max="20" value={status.leverage}
							onchange={(e) => updateConfig('leverage', e.currentTarget.value)} />
					</div>
					<div class="control-group">
						<label>Risk/Trade: {(status.risk_per_trade * 100).toFixed(1)}%</label>
						<input type="range" min="0.5" max="5" step="0.1" value={status.risk_per_trade * 100}
							onchange={(e) => updateConfig('risk_per_trade', (parseFloat(e.currentTarget.value) / 100).toString())} />
					</div>
					<div class="control-group">
						<label>Daily Loss Limit: {(status.daily_loss_limit * 100).toFixed(0)}%</label>
						<input type="range" min="1" max="20" step="1" value={status.daily_loss_limit * 100}
							onchange={(e) => updateConfig('daily_loss_limit', (parseFloat(e.currentTarget.value) / 100).toString())} />
					</div>
				{/if}
				<button class="btn btn-primary" onclick={runScreener}>Re-run Screener</button>
				<button class="btn" onclick={resetCircuit}>Reset Circuit Breaker</button>
			</div>

			<div class="card">
				<h3>Watchlist</h3>
				<div class="watchlist">
					{#each watchlist as coin}
						<span class="watchlist-chip">{coin}</span>
					{/each}
				</div>
			</div>
		</aside>

		<!-- Main Content -->
		<main class="main">
			<div class="tabs">
				<button class="tab {activeTab === 'positions' ? 'active' : ''}" onclick={() => activeTab = 'positions'}>
					Positions ({positions.length})
				</button>
				<button class="tab {activeTab === 'trades' ? 'active' : ''}" onclick={() => activeTab = 'trades'}>
					Trades ({trades.length})
				</button>
				<button class="tab {activeTab === 'signals' ? 'active' : ''}" onclick={() => activeTab = 'signals'}>
					Signals ({signals.length})
				</button>
			</div>

			{#if activeTab === 'positions'}
				<div class="card">
					{#if positions.length === 0}
						<p class="empty">No open positions</p>
					{:else}
						<table class="data-table">
							<thead>
								<tr>
									<th>Symbol</th>
									<th>Dir</th>
									<th>Entry</th>
									<th>SL</th>
									<th>TP</th>
									<th>Size</th>
									<th>Lev</th>
									<th></th>
								</tr>
							</thead>
							<tbody>
								{#each positions as pos}
									<tr>
										<td>{pos.symbol}</td>
										<td class={pos.direction === 'long' ? 'green' : 'red'}>{pos.direction.toUpperCase()}</td>
										<td>{pos.entry_price.toFixed(2)}</td>
										<td>{pos.stop_loss.toFixed(2)}</td>
										<td>{pos.take_profit.toFixed(2)}</td>
										<td>${pos.size_usdc.toFixed(0)}</td>
										<td>{pos.leverage}x</td>
										<td>
											<button class="btn" onclick={() => closePosition(pos.symbol)}>Close</button>
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
					{/if}
				</div>
			{:else if activeTab === 'trades'}
				<div class="card">
					{#if trades.length === 0}
						<p class="empty">No trades yet</p>
					{:else}
						<table class="data-table">
							<thead>
								<tr>
									<th>Time</th>
									<th>Symbol</th>
									<th>Dir</th>
									<th>Entry</th>
									<th>Exit</th>
									<th>P&L</th>
									<th>Status</th>
								</tr>
							</thead>
							<tbody>
								{#each trades as trade}
									<tr>
										<td>{new Date(trade.created_at).toLocaleTimeString()}</td>
										<td>{trade.symbol}</td>
										<td class={trade.direction === 'long' ? 'green' : 'red'}>{trade.direction.toUpperCase()}</td>
										<td>{trade.entry_price.toFixed(2)}</td>
										<td>{trade.exit_price ? trade.exit_price.toFixed(2) : '-'}</td>
										<td class={trade.pnl_usdc > 0 ? 'green' : trade.pnl_usdc < 0 ? 'red' : ''}>
											{trade.pnl_usdc ? formatPnL(trade.pnl_usdc) : '-'}
										</td>
										<td>
											<span class="badge {trade.status === 'open' ? 'badge-green' : 'badge-orange'}">
												{trade.status}
											</span>
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
					{/if}
				</div>
			{:else if activeTab === 'signals'}
				<div class="card">
					{#if signals.length === 0}
						<p class="empty">No signals yet</p>
					{:else}
						<table class="data-table">
							<thead>
								<tr>
									<th>Time</th>
									<th>Symbol</th>
									<th>Dir</th>
									<th>Confidence</th>
									<th>Reasoning</th>
								</tr>
							</thead>
							<tbody>
								{#each signals as sig}
									<tr>
										<td>{new Date(sig.created_at).toLocaleTimeString()}</td>
										<td>{sig.symbol}</td>
										<td class={sig.direction === 'long' ? 'green' : sig.direction === 'short' ? 'red' : ''}>
											{sig.direction.toUpperCase()}
										</td>
										<td>
											<div class="confidence-bar">
												<div class="confidence-fill" style="width: {sig.confidence * 100}%"></div>
											</div>
											<span>{(sig.confidence * 100).toFixed(0)}%</span>
										</td>
										<td class="reasoning">{sig.reasoning}</td>
									</tr>
								{/each}
							</tbody>
						</table>
					{/if}
				</div>
			{/if}
		</main>
	</div>
</div>

<style>
	.dashboard {
		padding: 1rem;
		max-width: 1400px;
		margin: 0 auto;
	}

	.header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 1rem;
		background: var(--bg-secondary);
		border-radius: 12px;
		margin-bottom: 1rem;
		border: 1px solid var(--bg-tertiary);
	}

	.header-left {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	.header-left h1 {
		margin: 0;
		font-size: 1.5rem;
	}

	.version {
		font-size: 0.75rem;
		color: var(--text-secondary);
		background: var(--bg-tertiary);
		padding: 0.25rem 0.5rem;
		border-radius: 4px;
	}

	.header-center {
		display: flex;
		gap: 0.5rem;
	}

	.header-right {
		text-align: right;
	}

	.stat-label {
		display: block;
		font-size: 0.75rem;
		color: var(--text-secondary);
	}

	.stat-value {
		font-size: 1.25rem;
		font-weight: 600;
	}

	.error-banner {
		background: rgba(255, 68, 85, 0.15);
		color: var(--accent-red);
		padding: 0.75rem 1rem;
		border-radius: 8px;
		margin-bottom: 1rem;
	}

	.main-grid {
		display: grid;
		grid-template-columns: 300px 1fr;
		gap: 1rem;
	}

	.sidebar {
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}

	.control-group {
		margin-bottom: 1rem;
	}

	.control-group label {
		display: block;
		font-size: 0.8rem;
		color: var(--text-secondary);
		margin-bottom: 0.25rem;
	}

	.control-group select,
	.control-group input {
		width: 100%;
		padding: 0.5rem;
		background: var(--bg-primary);
		color: var(--text-primary);
		border: 1px solid var(--bg-tertiary);
		border-radius: 6px;
	}

	.sidebar button {
		width: 100%;
		margin-bottom: 0.5rem;
	}

	.watchlist {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
	}

	.watchlist-chip {
		background: var(--bg-tertiary);
		padding: 0.25rem 0.5rem;
		border-radius: 4px;
		font-size: 0.8rem;
	}

	.tabs {
		display: flex;
		gap: 0.5rem;
		margin-bottom: 1rem;
	}

	.tab {
		background: var(--bg-secondary);
		color: var(--text-secondary);
		border: 1px solid var(--bg-tertiary);
		padding: 0.5rem 1rem;
		border-radius: 8px;
		cursor: pointer;
		font-size: 0.9rem;
	}

	.tab.active {
		background: var(--bg-tertiary);
		color: var(--text-primary);
	}

	.data-table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.85rem;
	}

	.data-table th,
	.data-table td {
		padding: 0.5rem;
		text-align: left;
		border-bottom: 1px solid var(--bg-tertiary);
	}

	.data-table th {
		color: var(--text-secondary);
		font-weight: 500;
	}

	.green { color: var(--accent-green); }
	.red { color: var(--accent-red); }

	.empty {
		color: var(--text-secondary);
		text-align: center;
		padding: 2rem;
	}

	.confidence-bar {
		width: 60px;
		height: 4px;
		background: var(--bg-tertiary);
		border-radius: 2px;
		overflow: hidden;
		display: inline-block;
		margin-right: 0.5rem;
		vertical-align: middle;
	}

	.confidence-fill {
		height: 100%;
		background: var(--accent-blue);
		border-radius: 2px;
	}

	.reasoning {
		max-width: 300px;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
</style>
