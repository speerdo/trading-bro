# FCM Trading Hours & Maintenance Windows

## Regular Trading Hours

Coinbase Financial Markets (FCM) **perp-style futures trade 24/7**, except during scheduled maintenance:

| Day | Hours (ET) |
|-----|-----------|
| Sunday – Thursday | 24 hours |
| Friday | 6:00 PM – 5:00 PM next day |
| **Friday Maintenance** | **5:00 PM – 6:00 PM ET: CLOSED** |

So effectively, perps trade almost continuously except for that **1-hour Friday window**.

## Maintenance Windows

### Weekly Maintenance
- **When**: Every Friday 5:00 PM – 6:00 PM ET
- **Duration**: 1 hour
- **Action**: Agent skips the signal loop during this window. Burt sends a proactive Discord reminder at 5PM ET if there are open positions.

### Quarterly Maintenance
- **When**: Scheduled quarterly (exact dates provided by Coinbase ~2 weeks ahead)
- **Duration**: ~3 hours
- **Action**: Hard-coded in `agent/maintenance.py` before the window.

## Before You Go Live

1. **Fund your futures account**: Sweep USD from spot → CFM. Your `futures_buying_power` must be > $0.
2. **Check maintenance calendar**: Visit [Coinbase Exchange Status](https://status.coinbase.com/) for any upcoming maintenance windows.
3. **Monitor open positions before Friday 5PM**: Consider closing or reducing risk before the weekly maintenance window.

## How the Agent Handles This

- **`agent/maintenance.py`**: Checks if current time is within a maintenance window
- **`agent/main.py`**: At the top of every signal loop iteration, calls `MaintenanceWindow.is_open()`. If `False`, skips iteration.
- **`agent/burt.py`**: Burt can send proactive reminders about upcoming maintenance.

## Comparison: FCM vs Offshore Venues

| Feature | Hyperliquid | Coinbase FCM |
|---------|-------------|--------------|
| Maintenance | None (always open) | Fri 5-6 PM ET + quarterly |
| Leverage | Up to 50x | Up to 10x (BTC/ETH), less for alts |
| Regulation | None | CFTC-regulated US DCM |
| Tax Treatment | Regular capital gains | Section 1256 (60/40) |
| USA Access | ❌ No | ✅ Yes |
