# MCP Server Management Makefile
# Simplified version with only essential commands

.PHONY: pre_start run_app run_app_alt test test_unit test_integration health_check stop help

# Colors for terminal output
RED=\033[0;31m
GREEN=\033[0;32m
YELLOW=\033[1;33m
BLUE=\033[0;34m
NC=\033[0m # No Color

help: ## Show this help message
	@echo "$(BLUE)MCP Server Management Commands:$(NC)"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(GREEN)%-12s$(NC) %s\n", $$1, $$2}'
	@echo ""

pre_start: ## Initialize environment, install dependencies, start Docker services and MCP servers
	@echo "$(YELLOW)🚀 Starting MCP environment setup...$(NC)"
	@echo "$(BLUE)� Checking Python version...$(NC)"
	@python_version=$$(python3 --version 2>&1 | grep -oE "3\.[0-9]+"); \
	if [ "$$python_version" = "3.10" ] || [ "$$python_version" = "3.11" ]; then \
		echo "$(GREEN)✅ Python $$python_version detected$(NC)"; \
	else \
		echo "$(RED)❌ Python 3.10 or 3.11 required, found: $$python_version$(NC)"; \
		echo "$(YELLOW)Please install Python 3.10 or 3.11 and retry$(NC)"; \
		exit 1; \
	fi
	@echo "$(BLUE)�📦 Initializing UV environment...$(NC)"
	uv init --quiet 2>/dev/null || true
	uv sync
	@echo "$(BLUE)🐍 Creating virtual environment with correct Python version...$(NC)"
	uv venv --python 3.11 --quiet 2>/dev/null || uv venv --python 3.10 --quiet 2>/dev/null || uv venv --quiet
	@echo "$(BLUE)📥 Installing requirements...$(NC)"
	uv pip install -r requirements.txt
	@echo "$(BLUE)🐳 Starting Docker services...$(NC)"
	docker compose up -d
	@echo "$(YELLOW)⏳ Waiting for Docker services to be ready...$(NC)"
	sleep 15
	@echo "$(BLUE)🔧 Starting MCP servers...$(NC)"
	docker compose up -d milvus_server websearch_server scirex_server mongo_server
	@echo "$(YELLOW)⏳ Waiting for MCP servers to initialize...$(NC)"
	sleep 10
	@echo "$(GREEN)✅ Environment setup complete!$(NC)"
	@echo "$(BLUE)🧪 Testing MCP connectivity...$(NC)"
	.venv/bin/python connectivity_test.py --startup

run_app: ## Run the main MCP application
	@echo "$(YELLOW)🏃 Running MCP application...$(NC)"
	@echo "$(BLUE)📱 Starting Streamlit UI...$(NC)"
	@echo "$(GREEN)🌐 Access the app at: http://localhost:8501$(NC)"
	.venv/bin/python -m streamlit run ui_streamlit.py --server.port 8501 --server.address 0.0.0.0

run_app_alt: ## Run the MCP application on alternative port 8506
	@echo "$(YELLOW)🏃 Running MCP application on port 8506...$(NC)"
	@echo "$(BLUE)📱 Starting Streamlit UI...$(NC)"
	@echo "$(GREEN)🌐 Access the app at: http://localhost:8506$(NC)"
	@echo "$(YELLOW)⚠️  Alternative URL: http://127.0.0.1:8506$(NC)"
	.venv/bin/python -m streamlit run ui_streamlit.py --server.port 8506 --server.address 0.0.0.0

test: ## Run MCP connectivity tests
	@echo "$(YELLOW)🧪 Running MCP connectivity tests...$(NC)"
	@echo "$(BLUE)� Testing MCP server connections...$(NC)"
	.venv/bin/python connectivity_test.py

test_unit: ## Run unit tests
	@echo "$(YELLOW)🧪 Running unit tests...$(NC)"
	@echo "$(BLUE)🔧 Testing individual components...$(NC)"
	.venv/bin/python -m pytest tests/test_unit.py -v

test_integration: ## Run integration tests
	@echo "$(YELLOW)🔗 Running integration tests...$(NC)"
	@echo "$(BLUE)📊 Testing end-to-end workflows...$(NC)"
	.venv/bin/python -m pytest tests/test_integration.py -v

health_check: ## Run health check tests
	@echo "$(BLUE)🏥 Running health checks...$(NC)"
	.venv/bin/python tests/health_check.py

stop: ## Stop all services (MCP servers and Docker containers)
	@echo "$(YELLOW)🛑 Stopping all MCP services...$(NC)"
	@echo "$(BLUE)📱 Stopping MCP servers...$(NC)"
	docker compose stop milvus_server websearch_server scirex_server mongo_server || true
	@echo "$(BLUE)🐳 Stopping Docker services...$(NC)"
	docker compose down
	@echo "$(GREEN)✅ All services stopped successfully!$(NC)"
