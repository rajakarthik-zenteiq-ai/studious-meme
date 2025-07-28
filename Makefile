# Start and stop all services
start: start_docker run_servers
	@echo "$(GREEN)🚀 All MCP services and Docker containers started!$(NC)"

stop: stop_servers
	@echo "$(YELLOW)🛑 Stopping Docker containers...$(NC)"
	@docker compose down -v --remove-orphans
	@echo "$(GREEN)✅ All MCP services and Docker containers stopped!$(NC)"
.PHONY: help build up down logs clean test lint format install dev run_servers run_app start_docker stop_servers setup setup-venv setup-inspector health-check test-rbac deploy-prod

# Default target
help:
	@echo "🔐 MCP Log Analytics Platform with RBAC"
	@echo "======================================"
	@echo ""
	@echo "🚀 Quick Start Commands:"
	@echo "  make setup       # Complete environment setup"
	@echo "  make start       # Start all services"
	@echo "  make test        # Run all tests"
	@echo "  make health-check # Check system health"
	@echo ""
	@echo "🐳 Docker Commands:"
	@echo "  make start_docker   # Start Docker services"
	@echo "  make stop_servers   # Stop MCP servers"
	@echo "  make logs           # View logs"
	@echo "  make clean          # Clean containers"
	@echo ""
	@echo "🛠️ Development:"
	@echo "  make dev            # Setup dev environment"
	@echo "  make install        # Install dependencies"
	@echo "  make lint           # Code linting"
	@echo "  make format         # Code formatting"
	@echo "  make test-rbac      # Test RBAC system"
	@echo ""
	@echo "🔧 Debug Commands:"
	@echo "  make inspect-mongo      # Inspect MongoDB server"
	@echo "  make inspect-milvus     # Inspect Milvus server"
	@echo "  make inspect-websearch  # Inspect WebSearch server"
	@echo "  make inspect-scirex     # Inspect SciREX server"
	@echo ""
	@echo "🎯 RBAC Commands:"
	@echo "  make test-rbac-basic    # Basic RBAC tests"
	@echo "  make test-rbac-full     # Full RBAC test suite"
	@echo ""
	@echo "📊 Metrics:"
	@echo "  make health-check       # System health check"
	@echo "  make metrics            # Show system metrics"
	@echo ""
	@echo "🚀 Quick Start Guide:"
	@echo "  1. make setup           # First time setup"
	@echo "  2. make start           # Start everything"
	@echo "  3. make run_app         # Launch web interface"
	@echo "  4. Open http://localhost:8501"

# Environment detection
IS_DOCKER := $(shell if [ -f /.dockerenv ]; then echo "true"; else echo "false"; fi)

# Colors
RED := \033[0;31m
GREEN := \033[0;32m
YELLOW := \033[0;33m
NC := \033[0m

# Quick setup
setup: setup-venv install setup-inspector start_docker
	@echo "$(GREEN)✅ Setup complete!$(NC)"
	@echo "$(GREEN)Ready to use! Run 'make run_app' to start$(NC)"

# Development setup
dev: setup-venv install setup-inspector
	@echo "$(GREEN)✅ Development environment ready!$(NC)"

# Install dependencies
install:
	@echo "$(YELLOW)📦 Installing dependencies...$(NC)"
	@. .venv/bin/activate && uv pip install -r requirements.txt
	@echo "$(GREEN)✅ Dependencies installed$(NC)"

# Environment setup
setup-venv:
	@echo "$(YELLOW)🔧 Creating virtual environment...$(NC)"
	@uv venv --python 3.10
	@echo "$(GREEN)✅ Virtual environment created$(NC)"
	@echo "$(GREEN)Run 'source .venv/bin/activate' to activate$(NC)"

# Docker commands

start_docker:
	@echo "$(YELLOW)🐳 Starting Docker services...$(NC)"
	@docker compose up -d
	@echo "$(GREEN)✅ Docker services started$(NC)"
	@echo "$(GREEN)Services:$(NC)"
	@echo "$(GREEN)  - MongoDB: localhost:27017$(NC)"
	@echo "$(GREEN)  - Redis: localhost:6379$(NC)"
	@echo "$(GREEN)  - Milvus: localhost:19530$(NC)"

stop_servers:
	@echo "$(YELLOW)🛑 Stopping all MCP servers...$(NC)"
	@-pkill -f "python.*server.py" 2>/dev/null || true
	@-rm -f *.pid 2>/dev/null || true
	@echo "$(GREEN)✅ Servers stopped$(NC)"

run_servers:
	@echo "$(YELLOW)🚀 Starting MCP servers...$(NC)"
	@mkdir -p logs
	@. .venv/bin/activate && \
	(cd mcp_servers/mongo_server && python server.py > ../../logs/mongo_server.log 2>&1 & echo $$! > ../../mongo-server.pid) && \
	(cd mcp_servers/milvus_server && python server.py > ../../logs/milvus_server.log 2>&1 & echo $$! > ../../milvus-server.pid) && \
	(cd mcp_servers/websearch_server && python server.py > ../../logs/websearch_server.log 2>&1 & echo $$! > ../../websearch-server.pid) && \
	(cd mcp_servers/scirex_server && python server.py > ../../logs/scirex_server.log 2>&1 & echo $$! > ../../scirex-server.pid)
	@sleep 5
	@echo "$(GREEN)✅ MCP servers started:$(NC)"
	@echo "$(GREEN)  - MongoDB: localhost:8100$(NC)"
	@echo "$(GREEN)  - Milvus: localhost:8110$(NC)"
	@echo "$(GREEN)  - WebSearch: localhost:8140$(NC)"
	@echo "$(GREEN)  - SciREX: localhost:8150$(NC)"

run_app:
	@echo "$(GREEN)🌐 Starting Streamlit web application...$(NC)"
	@. .venv/bin/activate && streamlit run ui_streamlit.py --server.port 8501 --server.address 0.0.0.0

# Development commands
lint:
	@echo "$(YELLOW)🔍 Running linting...$(NC)"
	@. .venv/bin/activate && flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
	@. .venv/bin/activate && mypy . --ignore-missing-imports

format:
	@echo "$(YELLOW)✨ Formatting code...$(NC)"
	@. .venv/bin/activate && black .
	@. .venv/bin/activate && isort .

test:
	@echo "$(YELLOW)🧪 Running tests...$(NC)"
	@. .venv/bin/activate && python -m pytest tests/ -v

# RBAC specific commands
test-rbac:
	@echo "$(YELLOW)🔐 Testing RBAC system...$(NC)"
	@. .venv/bin/activate && python -c "from agent.agent import create_agent; from utils.auth_utils import UserRole; import asyncio; async def test_roles():\n    for role in UserRole:\n        agent = create_agent();\n        await agent.initialize(role);\n        print(f'{role.value}: {len(agent.tools)} tools available');\n        await agent.cleanup();\nasyncio.run(test_roles())"
	@echo "$(GREEN)✅ RBAC tests passed$(NC)"

test-rbac-basic:
	@echo "$(YELLOW)🔐 Testing basic RBAC...$(NC)"
	@. .venv/bin/activate && python -m pytest tests/test_rbac.py -v

# Health and monitoring
health-check:
	@echo "$(YELLOW)🏥 Checking system health...$(NC)"
	@. .venv/bin/activate && python tests/health_check.py

metrics:
	@echo "$(YELLOW)📊 System metrics...$(NC)"
	@docker compose ps
	@docker stats --no-stream

# Inspector commands
setup-inspector:
	@echo "$(YELLOW)🔧 Setting up MCP Inspector...$(NC)"
	@which node > /dev/null || (echo "$(RED)❌ Node.js is required for MCP Inspector$(NC)" && exit 1)
	@which npx > /dev/null || (echo "$(RED)❌ npx is required for MCP Inspector$(NC)" && exit 1)
	@echo "$(GREEN)✅ Inspector ready$(NC)"

inspect-mongo:
	@echo "$(YELLOW)🔍 Inspecting MongoDB server...$(NC)"
	@. .venv/bin/activate && npx @modelcontextprotocol/inspector uv --directory . run python -m mcp_servers.mongo_server.server

inspect-milvus:
	@echo "$(YELLOW)🔍 Inspecting Milvus server...$(NC)"
	@. .venv/bin/activate && npx @modelcontextprotocol/inspector uv --directory . run python -m mcp_servers.milvus_server.server

inspect-websearch:
	@echo "$(YELLOW)🔍 Inspecting WebSearch server...$(NC)"
	@. .venv/bin/activate && npx @modelcontextprotocol/inspector uv --directory . run python -m mcp_servers.websearch_server.server

inspect-scirex:
	@echo "$(YELLOW)🔍 Inspecting SciREX server...$(NC)"
	@. .venv/bin/activate && npx @modelcontextprotocol/inspector uv --directory . run python -m mcp_servers.scirex_server.server

# Cleanup commands
clean:
	@echo "$(RED)🧹 Cleaning up...$(NC)"
	@docker compose down -v --remove-orphans
	@docker system prune -f
	@rm -rf .venv
	@rm -rf logs/*.log
	@rm -rf *.pid
	@echo "$(GREEN)✅ Cleanup complete$(NC)"

reset: clean setup
	@echo "$(GREEN)🔄 Complete reset done$(NC)"

# Deployment commands
deploy-prod:
	@echo "$(GREEN)🚀 Deploying to production...$(NC)"
	@docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

# Client commands
client:
	@. .venv/bin/activate && python mcp_client/main.py

agent:
	@. .venv/bin/activate && python -c "from agent.agent import create_agent; from utils.auth_utils import UserRole; import asyncio, sys; query = ' '.join(sys.argv[1:]) if len(sys.argv) > 1 else 'Hello'; agent = create_agent(); async def run(): await agent.initialize(UserRole.ADMIN); result = await agent.analyze(query=query, user_id='cli_user', conversation_id='cli_session'); print(result); await agent.cleanup(); asyncio.run(run())" $(QUERY)

# Development helpers
dev-logs:
	@echo "$(YELLOW)📋 Development logs...$(NC)"
	@tail -f logs/*.log 2>/dev/null || echo "No logs found"

dev-shell:
	@. .venv/bin/activate && /bin/bash

# Help for specific commands
help-rbac:
	@echo "$(GREEN)RBAC Quick Reference:$(NC)"
	@echo "  User Roles: admin, rnd, developer, analyst, viewer"
	@echo "  Set role: export USER_ROLE=admin"
	@echo "  API header: X-User-Role: analyst"