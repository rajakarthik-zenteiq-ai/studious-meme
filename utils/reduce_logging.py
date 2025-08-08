"""
Logging configuration to reduce verbose HTTP requests
"""
import logging
import os

def configure_logging():
    """Configure logging levels to reduce verbosity"""
    
    # Set general log level from environment
    log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
    logging.basicConfig(level=getattr(logging, log_level))
    
    # Reduce HTTP request logging verbosity
    # Set httpx to WARNING to reduce HTTP request logs
    logging.getLogger('httpx').setLevel(logging.WARNING)
    
    # Set mcp client to WARNING to reduce MCP protocol logs  
    logging.getLogger('mcp.client').setLevel(logging.WARNING)
    logging.getLogger('mcp.client.streamable_http').setLevel(logging.WARNING)
    
    # Keep important application logs at INFO
    logging.getLogger('agent.mcp_agent').setLevel(logging.INFO)
    logging.getLogger('agent.memory_manager').setLevel(logging.INFO)
    
    # Only show errors for highly verbose components
    logging.getLogger('urllib3').setLevel(logging.ERROR)
    logging.getLogger('requests').setLevel(logging.ERROR)
    
    print(f"✅ Logging configured: General={log_level}, HTTP=WARNING, MCP=WARNING")

# Auto-configure when imported
configure_logging()
