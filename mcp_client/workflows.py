# mcp_client/workflows.py

# This file is now optional since the agent handles workflows directly
# You can remove it or keep it for backward compatibility

import asyncio
from datetime import datetime
from typing import Any
from .client import create_mcp_client

class Workflows:
    """High-level orchestrations that use the agent"""
    
    def __init__(self):
        self.client = None
    
    async def initialize(self):
        self.client = await create_mcp_client()
    
    async def analyze_logs_comprehensive(self, date: str, user_id: str = None) -> dict:
        """Comprehensive log analysis"""
        if not self.client:
            await self.initialize()
        
        query = f"Analyze logs for date {date}"
        if user_id:
            query += f" for user {user_id}"
        
        response = await self.client.process_query(query)
        return {"analysis": response}
    
    async def cleanup(self):
        if self.client:
            await self.client.cleanup()