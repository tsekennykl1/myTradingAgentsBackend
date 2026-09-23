"""Manual end-to-end check of the backend MCP endpoint.

Steps:
  1. Pre-flight: send the raw `initialize` JSON-RPC request (same as the curl
     in the README) and print the HTTP status, so key/connection problems are
     obvious before any tool call.
  2. Open a real MCP session and call `create_analysis`.
  3. Print the run id and poll `get_analysis_status` a few times.

Usage:
  MCP_ACCESS_KEY=<key> python tests/test_mcp_call.py
  (or leave the env var unset and type the key when prompted)
"""

import asyncio
import os
import sys

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

MCP_URL = os.environ.get("MCP_URL", "http://127.0.0.1:8000/mcp/").rstrip("/") + "/"
STATUS_POLLS = 3          # how many status checks after creating the run
STATUS_POLL_SECONDS = 5   # pause between checks


def preflight_initialize(access_key: str) -> bool:
    """Send the same `initialize` request as the README curl and show the result."""
    headers = {
        "Authorization": f"Bearer {access_key}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test-mcp-call", "version": "1.0"},
        },
    }
    print(f"1) Pre-flight initialize -> POST {MCP_URL}")
    try:
        response = httpx.post(MCP_URL, headers=headers, json=payload, timeout=15)
    except httpx.ConnectError:
        print("   ✗ Could not connect. Is uvicorn running on 127.0.0.1:8000?")
        return False

    print(f"   HTTP {response.status_code}")
    body = response.text.strip()
    print(f"   {body[:300]}{'...' if len(body) > 300 else ''}")

    if response.status_code == 401:
        if "no MCP_ACCESS_KEY configured" in body:
            print("   ✗ The running server did not load MCP_ACCESS_KEY.")
            print("     Run: python app/initialSetup.py")
            print("     Then fully stop and restart uvicorn from the backend folder.")
        else:
            print("   ✗ The supplied key does not match the server's MCP_ACCESS_KEY.")
        return False
    if response.status_code != 200:
        print("   ✗ Unexpected status, aborting before the tool call.")
        return False
    print("   ✓ Key accepted, MCP session can start.")
    print()
    return True


async def analyze(ticker: str, date: str, access_key: str) -> None:
    headers = {"Authorization": f"Bearer {access_key}"}
    async with streamablehttp_client(MCP_URL, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            print(f"2) create_analysis {ticker} @ {date}")
            result = await session.call_tool("create_analysis", {
                "ticker": ticker,
                "analysis_date": date,
                "provider": "deepseek",
                "deep_model": "deepseek-chat",
                "quick_model": "deepseek-chat",
            })
            run_id = result.structuredContent["run_id"]
            print(f"   ✓ Analysis queued. run_id = {run_id}")
            print()

            print("3) get_analysis_status")
            for attempt in range(1, STATUS_POLLS + 1):
                status = await session.call_tool("get_analysis_status", {"run_id": run_id})
                data = status.structuredContent
                state = data.get("status") or data.get("state") or data
                print(f"   poll {attempt}/{STATUS_POLLS}: {state}")
                if str(state).lower() in {"ready", "completed", "failed", "cancelled"}:
                    break
                if attempt < STATUS_POLLS:
                    await asyncio.sleep(STATUS_POLL_SECONDS)
            print()
            print("Done. The run keeps going on the server; open the dashboard")
            print("or call get_analysis_status again later for the final result.")


if __name__ == "__main__":
    access_key = os.environ.get("MCP_ACCESS_KEY", "").strip()
    if not access_key:
        access_key = input("Enter the MCP access key: ").strip()
    if not access_key:
        sys.exit("No access key given. Set MCP_ACCESS_KEY or type it when prompted.")

    if not preflight_initialize(access_key):
        sys.exit(1)

    ticker = input("Enter the ticker: ").strip() or "0700.HK"
    date = input("Enter the date (YYYY-MM-DD): ").strip()
    asyncio.run(analyze(ticker, date, access_key))
