#!/usr/bin/env python3
"""
Production entrypoint for the MCP-based Log Analysis System.
"""
import asyncio
import argparse
import sys
import logging
from pathlib import Path

# Add the project root to the Python path
sys.path.insert(0, str(Path(__file__).parent))

from mcp_client.main import main as client_main
from agent.mcp_agent import MCPAgent


# Production logging
logging.basicConfig(level=logging.WARNING)


def create_fastapi_app():
    """Create and configure the FastAPI application."""
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from contextlib import asynccontextmanager
    from api.router import router as api_router
    from api.v1.endpoints.chat_endpoints import initialize_services, cleanup
    
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Application lifespan handler."""
        # Startup
        await initialize_services()
        yield
        # Shutdown
        await cleanup()
    
    # Create FastAPI app with lifespan
    app = FastAPI(
        title="MCP LLM Agent API",
        description="FastAPI backend with LangGraph-based MCP LLM agent",
        version="1.0.0",
        lifespan=lifespan
    )
    
    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Configure as needed for production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Include API router
    app.include_router(api_router)
    
    # Root endpoint
    @app.get("/")
    async def root():
        return {
            "message": "MCP LLM Agent API",
            "version": "1.0.0",
            "endpoints": {
                "chat": "/api/v1/chat",
                "health": "/api/v1/chat/health",
                "docs": "/docs"
            }
        }
    
    return app


def run_fastapi_server(host: str = "0.0.0.0", port: int = 8000):
    """Run the FastAPI server."""
    import uvicorn
    
    app = create_fastapi_app()
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info"
    )


def main():
    """Main entrypoint with CLI options."""
    parser = argparse.ArgumentParser(description="MCP Log Analysis System")
    parser.add_argument(
        "mode",
        choices=["client", "agent", "api"],
        help="Run mode: 'client' for MCP client, 'agent' for LangGraph agent, 'api' for FastAPI server"
    )
    parser.add_argument(
        "--query",
        type=str,
        help="Query to process (for agent mode)"
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host to bind FastAPI server (for api mode)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind FastAPI server (for api mode)"
    )
    
    args = parser.parse_args()
    
    if args.mode == "client":
        asyncio.run(client_main())
    elif args.mode == "agent":
        agent = MCPAgent()
        # Ensure initialization (defaulting to VIEWER role if not provided)
        if not getattr(agent, '_initialized', False):
            from utils.auth_utils import UserRole
            asyncio.run(agent.initialize(UserRole.VIEWER))
        if args.query:
            result = asyncio.run(agent.chat(
                query=args.query,
                user_id="cli_user",
                conversation_id="cli_session"
            ))
            print(result)
        else:
            logging.error("Please provide a --query for agent mode")
    elif args.mode == "api":
        logging.info(f"Starting FastAPI server on {args.host}:{args.port}")
        run_fastapi_server(host=args.host, port=args.port)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
