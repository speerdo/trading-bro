"""Test FastAPI backend endpoints."""
import asyncio, sys
sys.path.insert(0, '.')

import uvicorn
from agent.main import TradeBrainAgent
from agent.api import app, set_agent_state
from agent.coinbase_client import CoinbaseClient
from agent.executor import Executor
from agent.risk_manager import RiskManager
from agent.screener import Screener

async def test():
    # Set up agent state for API
    cb = CoinbaseClient()
    executor = Executor(cb)
    risk = RiskManager()
    screener = Screener(cb)
    
    # Run screener to populate watchlist
    watchlist = await screener.run(max_watchlist=3)
    
    set_agent_state(executor, risk, screener)
    
    # Start server in background
    config = uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    await asyncio.sleep(1)
    
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            # Test status endpoint
            r = await client.get("http://127.0.0.1:8000/api/status")
            print(f"Status: {r.status_code}")
            print(f"Body: {r.json()}")
            
            # Test trades endpoint
            r = await client.get("http://127.0.0.1:8000/api/trades")
            print(f"\nTrades: {r.status_code}, count={len(r.json())}")
            
            # Test watchlist endpoint
            r = await client.get("http://127.0.0.1:8000/api/watchlist")
            print(f"\nWatchlist: {r.status_code}")
            print(f"Body: {r.json()}")
            
            # Test positions endpoint
            r = await client.get("http://127.0.0.1:8000/api/positions")
            print(f"\nPositions: {r.status_code}, count={len(r.json())}")
    finally:
        server.should_exit = True
        await asyncio.sleep(0.5)
        await cb.close()
    
    print("\n✅ API test passed!")

if __name__ == "__main__":
    asyncio.run(test())
