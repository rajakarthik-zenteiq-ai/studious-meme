# MCP-Based Production AI Agent Platform

A production-grade FastAPI application with MCP (Model Context Protocol) architecture featuring RBAC, streaming, real-time data analysis, and multi-database integration.

## 🏗️ Architecture

**Modular MCP Server Design:**
- **MongoDB Server** (Port 8100): File storage, metadata, S3 integration
- **Milvus Server** (Port 8110): Vector database for embeddings
- **WebSearch Server** (Port 8140): Real-time web search capabilities  
- **SciREX Server** (Port 8150): ML analysis and data processing

**Core Components:**
- **FastAPI Production App**: RBAC, streaming, error handling
- **MCP Agent**: Dynamic tool discovery with role-based access
- **Multi-Database**: MongoDB, Redis, Milvus integration
- **Real-time Analysis**: Immediate data profiling and metadata enrichment

## 🚀 Quick Start

### Essential Commands
```bash
make pre_start      # Complete setup: environment, dependencies, Docker & MCP servers
make run_app        # Start Streamlit web application  
make stop          # Stop all services and cleanup
```

### Manual Setup
```bash
# 1. Environment setup
cp .env.sample .env  # Configure your settings

# 2. Start services
docker compose up -d

# 3. Test connectivity  
python3 tests/connectivity_test.py

# 4. Run application
streamlit run ui_streamlit.py
```

## 📁 Project Structure

```
mcp/
├── agent/          # MCP Agent core logic with RBAC
├── api/            # FastAPI production endpoints  
├── config/         # Configuration management
├── mcp_client/     # MCP client implementation
├── mcp_servers/    # MCP server implementations
│   ├── mongo_server/     # MongoDB + S3 storage
│   ├── milvus_server/    # Vector database
│   ├── websearch_server/ # Web search capabilities
│   └── scirex_server/    # ML analysis server
├── utils/          # Shared utilities
├── tests/          # Test suite
└── docs/           # Documentation
```

## 🧪 Testing

```bash
# Health check
python3 tests/connectivity_test.py

# File upload workflow
python3 tests/test_direct_upload.py

# Full test suite
pytest tests/
```

## 📊 Features

- ✅ **Production FastAPI** with RBAC, streaming, error handling
- ✅ **MCP Protocol** compliance with dynamic tool discovery  
- ✅ **Multi-database** support (MongoDB, Redis, Milvus)
- ✅ **S3 Storage** integration with automatic file classification
- ✅ **Real-time Analysis** with immediate metadata enrichment
- ✅ **User Context** propagation and role-based access control

## 📚 Documentation

See `docs/` folder for detailed documentation:
- Architecture and implementation guides
- Development history and fixes
- Test workflow procedures

---

**Status**: Production-ready MCP Agent system with full database connectivity and file upload workflow. ✅
