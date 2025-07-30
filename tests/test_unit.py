"""
Unit Tests for MCP Platform
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock
from mcp_client.client import MCPClient
from utils.auth_utils import UserRole

class TestMCPClient:
    """Unit tests for MCP Client"""
    
    @pytest.fixture
    def client(self):
        return MCPClient()
    
    def test_client_initialization(self, client):
        """Test client initialization"""
        assert client.provider == "openai"
        assert client.model == "gpt-4o-mini"
        assert client.current_role is None
    
    @pytest.mark.asyncio
    async def test_initialize_with_role(self, client):
        """Test client initialization with role"""
        # Mock the agent initialization
        client.agent.initialize = AsyncMock()
        
        await client.initialize(UserRole.ADMIN)
        
        assert client.current_role == UserRole.ADMIN
        client.agent.initialize.assert_called_once_with(UserRole.ADMIN)

class TestAuthUtils:
    """Unit tests for authentication utilities"""
    
    def test_user_roles(self):
        """Test user role enum"""
        assert UserRole.ADMIN.value == "admin"
        assert UserRole.USER.value == "user"
        assert UserRole.VIEWER.value == "viewer"
