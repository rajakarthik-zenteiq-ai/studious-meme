# Documentation

## Architecture & Implementation
- [Contributing Dependencies](CONTRIBUTING_DEPENDENCIES.md) - Dependencies and environment setup
- [Critical Changelog](CRITICAL_CHANGELOG.md) - Major system changes log
- [Test Workflow](TEST_WORKFLOW.md) - Testing procedures and workflows

## Development History & Fixes
- [Critical Fixes Summary](CRITICAL_FIXES_SUMMARY.md) - Summary of critical bug fixes
- [Final Fixes Summary](FINAL_FIXES_SUMMARY.md) - Latest implementation fixes  
- [Fixes Summary](FIXES_SUMMARY.md) - Historical fix documentation
- [Resolution Complete](RESOLUTION_COMPLETE.md) - Project completion status

## Project Structure
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

## Quick Start
1. **Setup Environment**: Copy `.env.sample` to `.env` and configure
2. **Start Services**: `docker compose up -d`
3. **Test Connectivity**: `python3 tests/connectivity_test.py`
4. **Run UI**: `streamlit run ui_streamlit.py`

## Key Features
- **Production FastAPI** with RBAC, streaming, error handling
- **MCP Protocol** compliance with dynamic tool discovery
- **Multi-database** support (MongoDB, Redis, Milvus)
- **S3 Storage** integration with automatic file classification
- **Real-time Analysis** with immediate metadata enrichment
- **User Context** propagation and role-based access control
