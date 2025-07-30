# OpenAI Web Search MCP Server Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY mcp_servers/websearch_server/requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy server files
COPY mcp_servers/websearch_server/server.py /app/
COPY mcp_servers/websearch_server/__init__.py /app/

# Copy config directory
COPY config /app/config/

# Set Python path to include the project root
ENV PYTHONPATH=/app:$PYTHONPATH

# Create necessary directories
RUN mkdir -p /app/data /app/logs

# Create non-root user for security
RUN useradd -m -u 1000 mcp && chown -R mcp:mcp /app
USER mcp

# Expose port
EXPOSE 8105

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8105/health || exit 1

# Run server
CMD ["python", "mcp_servers/websearch_server/server.py"]