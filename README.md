# MCP-Based Log Analytics & AI Platform

This project uses a modular MCP (Model Context Protocol) architecture:
- **4 MCP Servers**: each hosts a narrow set of tools over SSE.
- **LangGraph Agent**: a ReAct-style multi-agent orchestrator.
- **MCP Client**: unified Python backend interface for all servers.
- **FastAPI Integration**: OpenAI-compatible chat API endpoints.
- **Streamlit UI**: Web-based interface for easy interaction.
- **LLM Backends**: vLLM, OpenAI, Gemini (configurable).

## Services

| Service           | Port  | Description                            |
|-------------------|-------|----------------------------------------|
| MongoDB           | 27017 | Stores logs, chat history, files       |
| Redis             | 6379  | Cache & short-term memory              |
| Milvus            | 19530 | Vector similarity search               |
| Neo4j             | 7687  | Long-term knowledge graph memory       |
| vLLM              | 8001  | DeepSeek/Qwen3-8B LLM endpoint         |
| **FastAPI Server**| 8000  | **OpenAI-compatible chat endpoints**   |
| **Streamlit UI** | 8501  | **Web-based user interface**           |
| mongo_server      | 8100  | MCP tools for MongoDB                  |
| milvus_server     | 8110  | MCP tools for Milvus                   |
| websearch_server  | 8140  | MCP tools for WebSearch/OpenAI         |
| scirex_server     | 8150  | MCP tools for SciREX (AutoML models)   |

## Quick Start

### One-Command Setup
```bash
# 1. Setup environment
make setup-venv
source .venv/bin/activate
make install

# 2. Start Docker services (databases, vLLM)
make start_docker

# 3. Start all MCP servers in background
make run_servers

# 4. Start Streamlit web application
make run_app
```

The Streamlit application will be available at `http://localhost:8501`

### Alternative: FastAPI Server
Start the OpenAI-compatible API server:
```bash
python main.py api --host 0.0.0.0 --port 8000
```

API Endpoints:
- `POST /api/v1/chat/completions` - OpenAI-compatible chat completions with SSE streaming
- `GET /api/v1/chat/conversations/{id}` - Retrieve conversation history  
- `GET /api/v1/chat/health` - Health check and service status
- `GET /api/v1/chat/providers` - List available LLM providers
- `GET /docs` - Interactive API documentation

### Streamlit Web Interface
The easiest way to use the platform:
```bash
make run_app
```
Access the web interface at `http://localhost:8501`

### Interactive MCP Client
Run the MCP Client (interactive):
```bash
python main.py client
```

### Direct Agent Query
Test the LangGraph agent directly:
```bash
python main.py agent --query "Analyze the logs for error patterns"
```

## Make Commands

### 🚀 Quick Start Commands
```bash
make start_docker   # Start all Docker services (databases, vLLM)
make run_servers    # Start all MCP servers in background
make run_app        # Start Streamlit web application
make stop_servers   # Stop all MCP servers
```

### 🛠️ Development Commands
```bash
make help           # Show all available commands
make setup-venv     # Create virtual environment
make install        # Install Python dependencies
make dev            # Setup development environment
make test           # Run tests
make lint           # Run code linting
make format         # Format code
```

### 🐳 Docker Commands
```bash
make build          # Build all Docker images
make up             # Start all Docker services
make down           # Stop all Docker services
make logs           # View Docker logs
make clean          # Clean up containers and volumes
```

### 🔧 Individual Server Commands (for debugging)
```bash
make mongo-server     # Run MongoDB MCP server
make milvus-server    # Run Milvus MCP server
make websearch-server # Run WebSearch MCP server
make scirex-server    # Run SciREX MCP server
```

### 🔍 MCP Inspector Commands
```bash
make inspect-mongo     # Debug MongoDB MCP server
make inspect-milvus    # Debug Milvus MCP server
make inspect-websearch # Debug WebSearch MCP server
make inspect-scirex    # Debug SciREX MCP server
```

## FastAPI Integration

The FastAPI server provides a clean interface to the MCP LLM agent with:

- **OpenAI-compatible endpoints** for easy integration
- **SSE streaming** for real-time responses  
- **Dynamic LLM provider switching** (OpenAI, Gemini, vLLM)
- **Automatic tool calling** with MCP servers
- **Conversation memory** and context management
- **File attachment support** for data analysis

### Example Usage

```bash
# Start the server
python main.py api

# Test with curl
curl -X POST http://localhost:8000/api/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-User-ID: user123" \
  -H "X-Conversation-ID: conv456" \
  -d '{
    "message": "Hello, analyze my logs",
    "provider": "openai",
    "model": "gpt-4"
  }'
```

## Project Structure

```text
project_root/
├── mcp_servers/         # Four MCP servers (mongo, milvus, websearch, scirex)
├── agent/               # LangGraph ReAct agent
├── mcp_client/          # Python client & workflows
├── api/                 # FastAPI server
├── ui_streamlit.py      # Streamlit web interface
├── docker-compose.yml
├── Makefile            # All commands for easy usage
├── .env.sample
├── requirements.txt
└── README.md
```

## Environment Setup

1. **Copy environment file:**
   ```bash
   cp .env.sample .env
   # Edit .env with your credentials (OpenAI API key, etc.)
   ```

2. **Create virtual environment:**
   ```bash
   make setup-venv
   source .venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   make install
   ```

With this in place, you have a fully async, horizontally scalable AI platform ready for production!

## Troubleshooting

### Server Issues
- Check if Docker services are running: `make logs`
- Verify MCP servers status: `ls *.pid` (should show running servers)
- View server logs: `ls logs/` directory

### Port Conflicts
If you encounter port conflicts, modify the ports in:
- `docker-compose.yml` (for Docker services)
- Individual server files in `mcp_servers/*/server.py`

### Memory Issues
- Adjust vLLM memory settings in `docker-compose.yml`
- Monitor system resources: `docker stats`

## MCP Inspector (Debugging & Testing)

The MCP Inspector is a web-based tool for testing and debugging your MCP servers interactively.

### Prerequisites
- Node.js installed on your system
- Python dependencies installed (`make install`)

### Using Inspector

1. **Start Docker services**:
   ```bash
   make start_docker
   ```

2. **Launch Inspector for a specific server**:
   ```bash
   make inspect-mongo     # Inspect MongoDB server
   make inspect-milvus    # Inspect Milvus server
   make inspect-websearch # Inspect WebSearch server
   make inspect-scirex    # Inspect SciREX server
   ```

3. **Open your browser** to the provided URL to use the Inspector interface.

### Inspector Features

- **📊 Resources Tab**: View and test available resources
- **🔧 Tools Tab**: Test tool functions with custom inputs
- **📝 Prompts Tab**: Test prompt templates and arguments
- **🔔 Notifications**: Monitor server logs and messages
- **🔌 Connection**: Test different transport methods

## Configuration

### Environment Variables
Copy `.env.sample` to `.env` and configure:

```bash
# Database connections
MONGO_URI=mongodb://mongo:27017
REDIS_URL=redis://redis:6379/0
MILVUS_HOST=milvus
NEO4J_URI=bolt://neo4j:7687

# API Keys
OPENAI_API_KEY=your_openai_api_key

# MCP Server URLs
MONGODB_MCP_URL=http://localhost:8100/sse
MILVUS_MCP_URL=http://localhost:8110/sse
WEBSEARCH_MCP_URL=http://localhost:8140/sse
SCIREX_MCP_URL=http://localhost:8150/sse
```

## Usage Examples

### Basic Client Usage
```python
from mcp_client.client import MCPLogAnalyticsClient
from mcp_client.workflows import Workflows

# Initialize client
client = MCPLogAnalyticsClient()
workflows = Workflows(client)

# Store and analyze logs
await workflows.store_and_analyze_logs([
    {"timestamp": "2024-01-01", "level": "ERROR", "message": "Database connection failed"},
    {"timestamp": "2024-01-01", "level": "INFO", "message": "Service started"}
])

# Search similar errors
results = await workflows.find_similar_logs("database connection failed")
```

### Agent Usage
```bash
# Interactive agent
python main.py agent --query "What are the most common errors in the last hour?"

# Programmatic agent usage
from agent.agent import create_agent_graph

graph = create_agent_graph()
result = graph.invoke({
    "messages": [{"role": "user", "content": "Analyze system performance trends"}]
})
```

## Testing

### Integration Tests
```bash
make test
```

### Health Monitoring
```bash
python tests/health_check.py
```

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature-name`
3. Make your changes and test: `make test`
4. Format code: `make format`
5. Submit a pull request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

---

🚀 **Ready to go!** Start with `make help` to see all available commands.
```bash
python health_check.py
```

## Deployment

### Production Deployment
1. Configure environment variables in `.env`
2. Build and deploy:
   ```bash
   docker-compose up -d --build
   ```
3. Monitor health:
   ```bash
   python health_check.py
   ```

### Scaling
Individual MCP servers can be scaled horizontally:
```bash
docker-compose up -d --scale mongo_server=3
```

## Troubleshooting

### Common Issues
1. **Port conflicts**: Check if ports 8100-8150 are available
2. **Memory issues**: Increase Docker memory limits for Milvus/Neo4j
3. **Connection timeouts**: Ensure all services are healthy before starting clients

### Logs
```bash
docker compose logs -f [service_name]
```

### Reset Environment
```bash
make clean  # Remove all containers and volumes
./setup.sh  # Rebuild from scratch
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Run tests: `make test`
4. Format code: `make format`
5. Submit a pull request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

---

With this in place, you have a fully async, horizontally scalable AI platform ready for production!
