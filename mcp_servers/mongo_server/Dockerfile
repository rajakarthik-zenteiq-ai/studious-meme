FROM python:3.11-slim

# Install curl for health checks
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install dependencies
COPY mcp_servers/mongo_server/requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy server files
COPY mcp_servers/mongo_server/server.py /app/
COPY mcp_servers/mongo_server/__init__.py /app/
COPY mcp_servers/mongo_server/oauth_auth.py /app/

# Copy config directory
COPY config /app/config/

# Remove any conflicting bson package (pymongo provides its own bson)
RUN pip uninstall -y bson || true

# Set Python path to include the project root
ENV PYTHONPATH=/app:$PYTHONPATH

# Expose port
EXPOSE 8100

# Run server
CMD ["python", "/app/server.py", "--host", "0.0.0.0", "--port", "8100"]