from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
import os
import sys
import asyncio


#ACCESS_KEY = os.getenv("MCP_ACCESS_KEY_TEST", "")  # read from command-line argument or environment variable

async def analyze(ticker: str, date: str, access_key: str):
    ACCESS_KEY = access_key  # Use the provided access key
    async with streamablehttp_client(
        "http://127.0.0.1:8000/mcp",
        headers={"Authorization": f"Bearer {ACCESS_KEY}"},
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("create_analysis", {
                "ticker": ticker, "analysis_date": date,
                "provider": "deepseek", "deep_model": "deepseek-chat",
                "quick_model": "deepseek-chat",
            })
            run_id = result.structuredContent["run_id"]
if __name__ == "__main__":

    ticker = input("Enter the ticker: ")
    date = input("Enter the date (YYYY-MM-DD): ")
    access_key = input("Enter the MCP access key: ")
    asyncio.run(analyze(ticker, date, access_key))

