# mcp_client/main.py

import sys
import json
import asyncio
import logging

from .client import MCPLogAnalyticsClient
from .workflows import Workflows

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

async def interactive_mode(wf: Workflows):
    print("🚀 Interactive MCP Log Analytics CLI")
    print("Type 'quit' to exit.")
    while True:
        query = input("Query> ").strip()
        if not query or query.lower() in ("quit", "exit"):
            break
        try:
            response = await wf.process_query(query)
            print(json.dumps(response, indent=2, ensure_ascii=False))
        except Exception as e:
            logger.exception("Error processing query")

async def main():
    client = MCPLogAnalyticsClient()
    await client.connect()
    wf = Workflows(client)

    if len(sys.argv) > 1:
        # Single-shot mode
        query = " ".join(sys.argv[1:])
        result = await wf.process_query(query)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        # Interactive shell
        await interactive_mode(wf)

    await client.close()

if __name__ == "__main__":
    asyncio.run(main())
