.PHONY: help build up down logs clean test lint format install dev run_servers run_app start_docker stop_servers

# Default target
help:
	@echo "Available commands:"
	@echo ""
	@echo "🚀 Quick Start Commands:"
	@echo "  run_servers   - Start all MCP servers locally"
	@echo "  run_app       - Start Streamlit web application"
	@echo "  start_docker  - Start all Docker services"
	@echo "  stop_servers  - Stop all MCP servers"
	@echo ""
	@echo "🐳 Docker Commands:"
	@echo "  build        - Build all Docker images"
	@echo "  up           - Start all services"
	@echo "  down         - Stop all services"
	@echo "  logs         - View logs from all services"
	@echo "  clean        - Remove all containers and volumes"
	@echo ""
	@echo "🛠️  Development Commands:"
	@echo "  install       - Install Python dependencies with uv"
	@echo "  cleanup-cache - Clean up caches to free disk space"
	@echo "  check-disk    - Check disk usage"
	@echo "  test          - Run tests"
	@echo "  lint          - Run linting"
	@echo "  format        - Format code"
	@echo "  dev           - Start development environment"
	@echo ""
	@echo "🔧 Individual MCP Server Commands:"
	@echo "  mongo-server     - Run MongoDB MCP server locally"
	@echo "  milvus-server    - Run Milvus MCP server locally"
	@echo "  websearch-server - Run WebSearch MCP server locally"
	@echo "  scirex-server    - Run SciREX MCP server locally"
	@echo ""
	@echo "🔍 MCP Inspector Commands (requires Node.js):"
	@echo "  inspect-mongo     - Inspect MongoDB MCP server"
	@echo "  inspect-milvus    - Inspect Milvus MCP server"
	@echo "  inspect-websearch - Inspect WebSearch MCP server"
	@echo "  inspect-scirex    - Inspect SciREX MCP server"
	@echo ""
	@echo "🤖 Client Commands:"
	@echo "  client    - Run MCP client"
	@echo "  agent     - Run agent with QUERY='your query'"
	@echo ""
	@echo "Quick Start Guide:"
	@echo "  1. make install         # Install dependencies"
	@echo "  2. make start_docker    # Start Docker services"
	@echo "  3. make run_servers     # Start MCP servers"
	@echo "  4. make run_app         # Start Streamlit app"

# Docker commands
build:
	docker compose build

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f

clean:
	docker compose down -v --remove-orphans
	docker system prune -f

# Quick Start commands
start_docker:
	@echo "🐳 Starting all Docker services..."
	docker compose up -d
	@echo "✅ Docker services started. Services available:"
	@echo "   - MongoDB: localhost:27017"
	@echo "   - Redis: localhost:6379"
	@echo "   - Milvus: localhost:19530"
	@echo "   - Neo4j: localhost:7687"
	@echo "   - vLLM: localhost:8001"

run_servers:
	@echo "🚀 Starting all MCP servers in background..."
	@echo "Make sure virtual environment is activated and Docker services are running"
	@. .venv/bin/activate && \
	(cd mcp_servers/mongo_server && python server.py > ../../logs/mongo_server.log 2>&1 & echo $$! > ../../mongo-server.pid) && \
	(cd mcp_servers/milvus_server && python server.py > ../../logs/milvus_server.log 2>&1 & echo $$! > ../../milvus-server.pid) && \
	(cd mcp_servers/websearch_server && python server.py > ../../logs/websearch_server.log 2>&1 & echo $$! > ../../websearch-server.pid) && \
	(cd mcp_servers/scirex_server && python server.py > ../../logs/scirex_server.log 2>&1 & echo $$! > ../../scirex-server.pid)
	@mkdir -p logs
	@sleep 3
	@echo "✅ MCP servers started:"
	@echo "   - MongoDB MCP server: localhost:8100"
	@echo "   - Milvus MCP server: localhost:8110"
	@echo "   - WebSearch MCP server: localhost:8140"
	@echo "   - SciREX MCP server: localhost:8150"
	@echo "📋 Check logs in logs/ directory"
	@echo "🛑 To stop servers: make stop_servers"

stop_servers:
	@echo "🛑 Stopping all MCP servers..."
	@-pkill -f "python.*server.py" 2>/dev/null || true
	@-rm -f mongo-server.pid milvus-server.pid websearch-server.pid scirex-server.pid 2>/dev/null || true
	@echo "✅ All MCP servers stopped"

run_app:
	@echo "🌐 Starting Streamlit web application..."
	@echo "Make sure MCP servers are running first: make run_servers"
	@. .venv/bin/activate && streamlit run ui_streamlit.py --server.port 8501 --server.address 0.0.0.0
	@echo "🌐 Streamlit app available at: http://localhost:8501"

fix-docker:
	@echo "🔧 Fixing Docker daemon connection..."
	@./fix_docker.sh

# Development commands
install:
	@echo "🐍 Installing dependencies with Python 3.10..."
	@. .venv/bin/activate && uv pip install -r requirements.txt

setup-venv:
	@echo "🔧 Setting up virtual environment with Python 3.10..."
	@rm -rf .venv
	@uv venv --python 3.10
	@echo "✅ Virtual environment created. Run 'source .venv/bin/activate' to activate."

setup-inspector:
	@echo "🔧 Setting up MCP Inspector (requires Node.js)..."
	@which node > /dev/null || (echo "❌ Node.js is required for MCP Inspector. Please install it first." && exit 1)
	@which npx > /dev/null || (echo "❌ npx is required for MCP Inspector. Please install Node.js first." && exit 1)
	@echo "✅ Node.js and npx are available. MCP Inspector ready to use."

dev: setup-venv install setup-inspector start_docker
	@echo "🚀 Development environment ready!"
	@echo "Run 'make run_servers' to start MCP servers"
	@echo "Run 'make run_app' to start Streamlit application"

test:
	python -m pytest tests/ -v

lint:
	flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
	mypy . --ignore-missing-imports

format:
	black .
	isort .

# Individual MCP server commands (for development/debugging)
mongo-server:
	@echo "🚀 Starting MongoDB MCP server..."
	@. .venv/bin/activate && cd mcp_servers/mongo_server && python server.py

milvus-server:
	@echo "🚀 Starting Milvus MCP server..."
	@. .venv/bin/activate && cd mcp_servers/milvus_server && python server.py

websearch-server:
	@echo "🚀 Starting WebSearch MCP server..."
	@. .venv/bin/activate && cd mcp_servers/websearch_server && python server.py

scirex-server:
	@echo "🚀 Starting SciREX MCP server..."
	@. .venv/bin/activate && cd mcp_servers/scirex_server && python server.py

# Client commands
client:
	@. .venv/bin/activate && python main.py client

agent:
	@. .venv/bin/activate && python main.py agent --query "$(QUERY)"

# MCP Inspector commands
inspect-mongo:
	@echo "🔍 Starting MCP Inspector for MongoDB server..."
	@echo "Make sure MongoDB server is running first: make mongo-server"
	npx @modelcontextprotocol/inspector uv --directory . run python -m mcp_servers.mongo_server.server

inspect-milvus:
	@echo "🔍 Starting MCP Inspector for Milvus server..."
	@echo "Make sure Milvus server is running first: make milvus-server"
	npx @modelcontextprotocol/inspector uv --directory . run python -m mcp_servers.milvus_server.server

inspect-websearch:
	@echo "🔍 Starting MCP Inspector for WebSearch server..."
	@echo "Make sure WebSearch server is running first: make websearch-server"
	npx @modelcontextprotocol/inspector uv --directory . run python -m mcp_servers.websearch_server.server

inspect-scirex:
	@echo "🔍 Starting MCP Inspector for SciREX server..."
	@echo "Make sure SciREX server is running first: make scirex-server"
	npx @modelcontextprotocol/inspector uv --directory . run python -m mcp_servers.scirex_server.server

cleanup-cache:
	@echo "🧹 Cleaning up caches to free disk space..."
	@echo "This will remove cached packages and models (they can be re-downloaded)"
	@read -p "Are you sure? (y/N) " -n 1 -r; echo; \
	if [[ $$REPLY =~ ^[Yy]$$ ]]; then \
		echo "🗑️  Removing Hugging Face cache (~348GB)..."; \
		rm -rf ~/.cache/huggingface; \
		echo "🗑️  Removing UV cache (~87GB)..."; \
		rm -rf ~/.cache/uv; \
		echo "🗑️  Removing pip cache (~53GB)..."; \
		rm -rf ~/.cache/pip; \
		echo "✅ Cleanup complete! Freed ~488GB of space"; \
	else \
		echo "❌ Cleanup cancelled"; \
	fi

check-disk:
	@echo "💾 Disk usage summary:"
	@df -h /home
	@echo ""
	@echo "📁 Largest directories in home:"
	@du -h --max-depth=1 ~ | sort -hr | head -10
