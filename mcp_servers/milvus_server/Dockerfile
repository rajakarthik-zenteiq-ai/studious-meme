# Milvus MCP Server Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy the entire project to get config and utils
COPY . /app

# Install dependencies
RUN pip install --no-cache-dir -r /app/requirements.txt

# Set Python path to include the project root
ENV PYTHONPATH=/app:$PYTHONPATH

# Create necessary directories
RUN mkdir -p /app/data /app/logs

# Create non-root user for security
RUN useradd -m -u 1000 mcp && chown -R mcp:mcp /app
USER mcp

# Expose port
EXPOSE 8102

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8102/health || exit 1

# Run server
WORKDIR /app/mcp_servers/milvus_server
CMD ["python", "server.py"]