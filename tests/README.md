# Production Test Suite

## Overview
This directory contains production-ready tests for the MCP Agent system.

## Test Files

### Core Functionality Tests
- `connectivity_test.py` - Tests MCP server connectivity and health
- `test_direct_upload.py` - Direct file upload workflow testing
- `test_file_upload.py` - Complete file upload and metadata testing
- `test_health_check.py` - Database and storage health verification
- `e2e_smoke_test.py` - End-to-end smoke testing

### Unit & Integration Tests  
- `test_unit.py` - Unit tests for individual components
- `test_integration.py` - Integration tests for component interactions
- `health_check.py` - Health check utilities
- `conftest.py` - PyTest configuration and fixtures

## Running Tests

### Quick Health Check
```bash
python3 tests/connectivity_test.py
python3 tests/test_health_check.py
```

### File Upload Testing
```bash
python3 tests/test_direct_upload.py
python3 tests/test_file_upload.py
```

### Full Test Suite
```bash
pytest tests/
```

### E2E Smoke Test
```bash
python3 tests/e2e_smoke_test.py
```

## Test Requirements
- All MCP servers must be running (MongoDB, Milvus, WebSearch, SciREX)
- Database must be accessible with proper credentials
- S3 storage credentials optional (graceful degradation for storage tests)

## Expected Results
- ✅ All MCP servers responding
- ✅ Database connectivity established  
- ✅ File metadata storage working
- ✅ Quick analysis pipeline functional
- ⚠️ S3 upload may fail without valid credentials (expected behavior)
