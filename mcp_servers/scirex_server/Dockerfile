# SciRex MCP Server Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies including Fortran compiler and scientific computing libraries
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    gfortran \
    libblas-dev \
    liblapack-dev \
    libatlas-base-dev \
    libopenblas-dev \
    curl \
    git \
    build-essential \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip and install wheel for better build performance
RUN pip install --upgrade pip wheel setuptools

# Copy server code and SciREX repo
COPY mcp_servers/scirex_server /app/mcp_servers/scirex_server
COPY config /app/config

# Pre-install scientific computing stack from wheels to avoid build issues
RUN pip install --no-cache-dir --only-binary=all \
    numpy>=1.24.0 \
    scipy>=1.10.0 \
    pandas>=2.0.0 \
    scikit-learn>=1.4.0 \
    matplotlib>=3.4.0 \
    seaborn>=0.11.0 \
    joblib>=1.3.0

# Install remaining server dependencies
RUN pip install --no-cache-dir -r mcp_servers/scirex_server/requirements.txt

# Install SciREX in editable mode with minimal dependencies first, then add extras if needed
WORKDIR /app/mcp_servers/scirex_server/SciREX

# Try to install SciREX core first, then extras separately
RUN pip install --default-timeout=1000 --retries=5 -e . || \
    echo "SciREX core installation failed, will use fallback implementations"

# Try to install additional dependencies for SciREX extras (optional)
RUN pip install --default-timeout=600 --retries=3 \
    jax jaxlib equinox optax xgboost tensorflow || \
    echo "Optional SciREX extras installation failed, core functionality will still work"

# Return to main working directory
WORKDIR /app

# Create necessary directories
RUN mkdir -p /app/data /app/logs

# Create non-root user for security
RUN useradd -m -u 1000 mcp && chown -R mcp:mcp /app
USER mcp

# Set Python path
ENV PYTHONPATH=/app:$PYTHONPATH

# Expose port
EXPOSE 8106

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8106/health || exit 1

# Run server
WORKDIR /app/mcp_servers/scirex_server
CMD ["python", "server.py"]