#!/usr/bin/env python3
"""
Comprehensive health check script for MCP Log Analytics Platform
"""
import asyncio
import httpx
import json
from datetime import datetime
from typing import Dict, Any, List
import sys

# Import settings
from config.settings import (
    MONGODB_MCP_URL, REDIS_MCP_URL, MILVUS_MCP_URL,
    NEO4J_MCP_URL, WEBSEARCH_MCP_URL, SCIREX_MCP_URL,
    MONGO_URI, REDIS_URL, NEO4J_URI, MILVUS_HOST, MILVUS_PORT
)

class HealthChecker:
    """System health checker for all services."""
    
    def __init__(self):
        self.services = {
            "mongodb": MONGODB_MCP_URL,
            "redis": REDIS_MCP_URL,
            "milvus": MILVUS_MCP_URL,
            "neo4j": NEO4J_MCP_URL,
            "websearch": WEBSEARCH_MCP_URL,
            "scirex": SCIREX_MCP_URL
        }
        self.results = {}
        
    async def check_mcp_server(self, name: str, url: str) -> Dict[str, Any]:
        """Check health of an MCP server."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{url}/health")
                response.raise_for_status()
                data = response.json()
                
                return {
                    "status": "healthy",
                    "response_time": response.elapsed.total_seconds(),
                    "details": data
                }
        except httpx.TimeoutException:
            return {"status": "timeout", "error": "Request timed out"}
        except httpx.HTTPStatusError as e:
            return {"status": "error", "error": f"HTTP {e.response.status_code}"}
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    async def check_database_direct(self, db_type: str) -> Dict[str, Any]:
        """Direct database connectivity check."""
        try:
            if db_type == "mongodb":
                from motor.motor_asyncio import AsyncIOMotorClient
                client = AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=5000)
                await client.admin.command('ping')
                client.close()
                return {"status": "connected"}
                
            elif db_type == "redis":
                import redis.asyncio as redis
                client = await redis.from_url(REDIS_URL)
                await client.ping()
                await client.close()
                return {"status": "connected"}
                
            elif db_type == "neo4j":
                from neo4j import AsyncGraphDatabase
                driver = AsyncGraphDatabase.driver(NEO4J_URI)
                await driver.verify_connectivity()
                await driver.close()
                return {"status": "connected"}
                
            elif db_type == "milvus":
                from pymilvus import connections, utility
                connections.connect(host=MILVUS_HOST, port=MILVUS_PORT)
                utility.list_collections()
                connections.disconnect("default")
                return {"status": "connected"}
                
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    async def run_full_check(self) -> Dict[str, Any]:
        """Run comprehensive health check."""
        print("🏥 MCP Log Analytics Platform Health Check")
        print("=" * 60)
        print(f"Started at: {datetime.now().isoformat()}")
        print("=" * 60)
        
        # Check MCP servers
        print("\n📡 Checking MCP Servers...")
        mcp_tasks = [
            self.check_mcp_server(name, url)
            for name, url in self.services.items()
        ]
        mcp_results = await asyncio.gather(*mcp_tasks)
        
        for (name, _), result in zip(self.services.items(), mcp_results):
            self.results[f"mcp_{name}"] = result
            status_icon = "✅" if result["status"] == "healthy" else "❌"
            print(f"  {status_icon} {name}: {result['status']}")
            if result["status"] == "healthy":
                print(f"     Response time: {result['response_time']:.3f}s")
                if "details" in result and "mcp" in result["details"]:
                    tools_count = result["details"]["mcp"].get("tools_count", 0)
                    print(f"     Tools available: {tools_count}")
            else:
                print(f"     Error: {result.get('error', 'Unknown error')}")
        
        # Check databases directly
        print("\n💾 Checking Database Connections...")
        db_types = ["mongodb", "redis", "neo4j", "milvus"]
        db_tasks = [self.check_database_direct(db) for db in db_types]
        db_results = await asyncio.gather(*db_tasks)
        
        for db_type, result in zip(db_types, db_results):
            self.results[f"db_{db_type}"] = result
            status_icon = "✅" if result["status"] == "connected" else "❌"
            print(f"  {status_icon} {db_type}: {result['status']}")
            if result["status"] == "error":
                print(f"     Error: {result.get('error', 'Unknown error')}")
        
        # Summary
        print("\n" + "=" * 60)
        print("📊 Summary:")
        
        healthy_services = sum(
            1 for k, v in self.results.items()
            if v["status"] in ["healthy", "connected"]
        )
        total_services = len(self.results)
        
        if healthy_services == total_services:
            print("✅ All systems operational!")
            return_code = 0
        elif healthy_services > total_services / 2:
            print(f"⚠️  Partial outage: {healthy_services}/{total_services} services healthy")
            return_code = 1
        else:
            print(f"❌ Major outage: only {healthy_services}/{total_services} services healthy")
            return_code = 2
        
        # Write detailed report
        report_file = f"health_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(report_file, 'w') as f:
            json.dump({
                "timestamp": datetime.now().isoformat(),
                "summary": {
                    "healthy_services": healthy_services,
                    "total_services": total_services,
                    "status": "healthy" if return_code == 0 else "degraded"
                },
                "details": self.results
            }, f, indent=2)
        
        print(f"\n📄 Detailed report saved to: {report_file}")
        print("=" * 60)
        
        return return_code

async def main():
    """Run health check and exit with appropriate code."""
    checker = HealthChecker()
    exit_code = await checker.run_full_check()
    sys.exit(exit_code)

if __name__ == "__main__":
    asyncio.run(main())