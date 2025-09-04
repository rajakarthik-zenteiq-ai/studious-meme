"""
Production MCP Client with RBAC integration
"""
import os
import sys
import asyncio
import logging
from typing import Dict, Any, List, Optional
import json

# Import auth utilities
from utils.auth_utils import AuthManager, UserRole

# LangChain MCP Adapters
from langchain_mcp_adapters.client import MultiServerMCPClient

# Local imports
from agent.mcp_agent import MCPAgent

# Production logging setup
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

class MCPClient:
    """
    Production MCP client with agent integration and RBAC
    """
    
    def __init__(self, provider: str = "openai", model: str = "gpt-4o-mini"):
        self.provider = provider
        self.model = model
        self.agent = MCPAgent()
        self.auth_manager = AuthManager()
        self.current_role: Optional[UserRole] = None
    
    async def initialize(self, user_role: UserRole = UserRole.VIEWER):
        """Initialize agent with user role"""
        self.current_role = user_role
        
        # Create UserContext for the agent
        from agent.rbac_system import get_rbac_manager
        rbac = get_rbac_manager()
        user_context = rbac.create_user_context(f"client_{id(self)}", user_role)
        await self.agent.initialize(user_context)
        
        # Filter accessible servers based on role
        accessible_servers = self._get_accessible_servers(user_role)
        
        # Send initialization notification to all accessible servers
        await self._send_initialized_notifications(accessible_servers)
    
    async def _send_initialized_notifications(self, servers: Dict[str, str]):
        """Send MCP initialized notifications to servers"""
        for server_name, server_config in servers.items():
            try:
                await self._send_initialized_request(server_config['url'])
            except Exception as e:
                logger.warning(f"Failed to initialize {server_name}: {e}")
    
    async def _send_initialized_request(self, server_url: str):
        """Send MCP initialized notification to a server"""
        import httpx
        
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream, application/json"
        }
        
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Step 1: Initialize session
            initialize_request = {
                "jsonrpc": "2.0",
                "id": f"init_{server_url.split('/')[-2] if server_url.endswith('/') else server_url.split('/')[-1]}",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {}
                    },
                    "clientInfo": {
                        "name": "mcp-client",
                        "version": "1.0.0"
                    }
                }
            }
            
            init_response = await client.post(server_url, json=initialize_request, headers=headers)
            
            if init_response.status_code != 200:
                raise Exception(f"Initialization failed: HTTP {init_response.status_code}")
                
            # Extract session ID from headers
            session_id = init_response.headers.get('mcp-session-id')
            if not session_id:
                raise Exception("No session ID returned from server")
            
            # Step 2: Send initialized notification with session ID
            initialized_request = {
                "jsonrpc": "2.0",
                "method": "notifications/initialized"
            }
            
            session_headers = headers.copy()
            session_headers['mcp-session-id'] = session_id
            
            notify_response = await client.post(
                server_url,
                json=initialized_request,
                headers=session_headers
            )
            
            if notify_response.status_code not in [200, 202, 204]:
                raise Exception(f"Notification failed: HTTP {notify_response.status_code}")
                
            return session_id
    
    def _get_accessible_servers(self, user_role: UserRole) -> Dict[str, str]:
        """Get accessible servers for the user role"""
        permissions = self.auth_manager.role_permissions.get(user_role)
        if not permissions:
            return {}
        
        config = {
            "mongodb": {"url": "http://localhost:8100/mcp/"},
            "milvus": {"url": "http://localhost:8110/mcp/"},
            "websearch": {"url": "http://localhost:8140/mcp/"},
            "scirex": {"url": "http://localhost:8150/mcp/"}
        }
        
        # Filter based on server access
        accessible = {}
        for server, access in permissions["servers"].items():
            if access != "none":
                accessible[server] = config[server]
        
        return accessible
    
    async def process_query(
        self, 
        query: str, 
        user_id: str = "default", 
        conversation_id: str = None,
        user_role: UserRole = UserRole.VIEWER
    ) -> str:
        """Process query with role-based access control and improved error handling"""
        if not conversation_id:
            import uuid
            conversation_id = str(uuid.uuid4())
        
        # Initialize with user role
        await self.initialize(user_role)
        
        # Check if query involves restricted tools
        try:
            # Use the agent chat API (LLM+LangGraph planner)
            result = await self.agent.chat(
                query=query,
                user_id=user_id,
                conversation_id=conversation_id,
                user_role=user_role,
            )
            # Enhanced error handling for common issues
            if isinstance(result, str) and "not found" in result.lower() and ("file" in query.lower() or "dataset" in query.lower()):
                enhanced_result = result + "\n\n💡 **Troubleshooting suggestions:**\n"
                enhanced_result += "1. Check if the file exists in the database using: `check the db for dataset`\n"
                enhanced_result += "2. Verify the file ID is correct (use `list_uploaded_files` tool)\n"
                enhanced_result += "3. Ensure you have permission to access this file\n"
                enhanced_result += "4. Try uploading the dataset again if it's missing\n"
                enhanced_result += "5. Check if the file is stored in S3 or GridFS storage\n"
                return enhanced_result
            if isinstance(result, str) and "clustering" in query.lower() and ("error" in result.lower() or "failed" in result.lower()):
                enhanced = result + "\n\n🔧 **For clustering analysis:**\n"
                enhanced += "1. Ensure the dataset is properly formatted (CSV with numeric columns)\n"
                enhanced += "2. Check that the file was uploaded successfully\n"
                enhanced += "3. Verify the dataset contains the expected columns for clustering\n"
                enhanced += "4. Try using a local file if the database version has issues\n"
                return enhanced
            return result
        except Exception as e:
            if "permission denied" in str(e).lower():
                return f"❌ Access denied: {e}"
            error_msg = f"❌ An error occurred while processing your request: {str(e)}\n\n"
            error_msg += "🔍 **Diagnostic information:**\n"
            error_msg += f"- User role: {user_role.value}\n"
            error_msg += f"- Query type: {'Dataset/File operation' if any(keyword in query.lower() for keyword in ['file', 'dataset', 'download', 'upload']) else 'General query'}\n"
            error_msg += f"- Available servers: {list(self._get_accessible_servers(user_role).keys())}\n"
            if "file" in query.lower() or "dataset" in query.lower():
                error_msg += "\n💭 **Suggested actions:**\n"
                error_msg += "1. Try checking what files are available in the database\n"
                error_msg += "2. Verify the file ID or filename is correct\n"
                error_msg += "3. Check if you have the right permissions for this operation\n"
            return error_msg
    
    async def check_tool_access(
        self, 
        user_role: UserRole, 
        server_name: str, 
        tool_name: str
    ) -> bool:
        """Check if user has access to specific tool"""
        return self.auth_manager.check_tool_access(user_role, server_name, tool_name) != "none"
    
    async def get_user_permissions(self, user_role: UserRole) -> Dict[str, Any]:
        """Get permissions for user role"""
        return self.auth_manager.get_user_permissions_summary(user_role)
    
    async def health_check(self, user_role: UserRole = UserRole.VIEWER) -> Dict[str, Any]:
        """Check health of accessible servers based on role"""
        results = {}
        
        accessible_servers = self._get_accessible_servers(user_role)
        
        for server_name, server_config in accessible_servers.items():
            try:
                import httpx
                # MCP servers require specific headers for HTTP transport
                headers = {
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream, application/json"
                }
                
                async with httpx.AsyncClient(timeout=10.0) as client:
                    # Step 1: Initialize session
                    initialize_request = {
                        "jsonrpc": "2.0",
                        "id": f"init_{server_name}",
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {
                                "tools": {}
                            },
                            "clientInfo": {
                                "name": "mcp-client",
                                "version": "1.0.0"
                            }
                        }
                    }
                    
                    init_response = await client.post(server_config['url'], json=initialize_request, headers=headers)
                    
                    if init_response.status_code != 200:
                        results[server_name] = {
                            "status": "error",
                            "url": server_config['url'],
                            "error": f"Init failed: HTTP {init_response.status_code}",
                            "accessible": True
                        }
                        continue
                    
                    # Extract session ID
                    session_id = init_response.headers.get('mcp-session-id')
                    if not session_id:
                        results[server_name] = {
                            "status": "error",
                            "url": server_config['url'],
                            "error": "No session ID returned",
                            "accessible": True
                        }
                        continue
                    
                    # Step 2: Send initialized notification
                    initialized_request = {
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized"
                    }
                    
                    session_headers = headers.copy()
                    session_headers['mcp-session-id'] = session_id
                    
                    await client.post(server_config['url'], json=initialized_request, headers=session_headers)
                    
                    # Step 3: Health check with session
                    mcp_request = {
                        "jsonrpc": "2.0",
                        "id": f"health_check_{server_name}",
                        "method": "tools/call",
                        "params": {
                            "name": "health_check",
                            "arguments": {}
                        }
                    }
                    
                    response = await client.post(
                        server_config['url'],
                        json=mcp_request,
                        headers=session_headers
                    )
                    
                    if response.status_code == 200:
                        # Parse SSE response
                        response_text = response.text
                        if response_text.startswith('event:'):
                            # Parse SSE format
                            lines = response_text.strip().split('\n')
                            data_line = None
                            for line in lines:
                                if line.startswith('data: '):
                                    data_line = line[6:]
                                    break
                            if data_line:
                                result_data = json.loads(data_line)
                            else:
                                result_data = {"error": "Could not parse SSE response"}
                        else:
                            result_data = response.json()
                        
                        results[server_name] = {
                            "status": "healthy",
                            "url": server_config['url'],
                            "response_time": response.elapsed.total_seconds(),
                            "accessible": True,
                            "data": result_data.get("result", result_data),
                            "session_id": session_id
                        }
                    else:
                        results[server_name] = {
                            "status": "error",
                            "url": server_config['url'],
                            "error": f"HTTP {response.status_code}: {response.text}",
                            "accessible": True
                        }
            except Exception as e:
                results[server_name] = {
                    "status": "error",
                    "url": server_config['url'],
                    "error": str(e),
                    "accessible": True
                }
        
        return {
            "role": user_role.value,
            "accessible_servers": results,
            "permissions": await self.get_user_permissions(user_role)
        }
    
    async def cleanup(self):
        """Clean up all resources"""
        await self.agent.cleanup()

    async def diagnose_file_issue(self, file_id: str, user_role: UserRole = UserRole.VIEWER) -> Dict[str, Any]:
        """Diagnose file download issues and provide detailed information"""
        await self.initialize(user_role)
        
        diagnosis = {
            "file_id": file_id,
            "user_role": user_role.value,
            "accessible_servers": list(self._get_accessible_servers(user_role).keys()),
            "issues_found": [],
            "suggestions": []
        }
        
        try:
            # Check if MongoDB server is accessible
            if "mongodb" not in diagnosis["accessible_servers"]:
                diagnosis["issues_found"].append("MongoDB server not accessible for your role")
                diagnosis["suggestions"].append("Contact admin to get MongoDB access")
                return diagnosis
            
            # Try to get file metadata first
            metadata_result = await self.agent.chat(
                query=f"Find file metadata for file_id: {file_id}",
                user_id="diagnostic",
                conversation_id="diagnostic",
                user_role=user_role,
            )
            
            if isinstance(metadata_result, str) and "not found" in metadata_result.lower():
                diagnosis["issues_found"].append("File metadata not found in database")
                diagnosis["suggestions"].extend([
                    "Check if the file ID is correct",
                    "List all uploaded files to verify the file exists",
                    "Try re-uploading the file if it's missing",
                ])
            else:
                diagnosis["metadata_found"] = True
                
                # Try to download the file
                download_result = await self.agent.chat(
                    query=f"Download file with ID: {file_id}",
                    user_id="diagnostic",
                    conversation_id="diagnostic",
                    user_role=user_role,
                )
                
                if isinstance(download_result, str) and ("error" in download_result.lower() or "failed" in download_result.lower()):
                    diagnosis["issues_found"].append("File download failed")
                    if "access denied" in download_result.lower():
                        diagnosis["suggestions"].append("You may not have permission to download this file")
                    elif "s3" in download_result.lower():
                        diagnosis["suggestions"].extend([
                            "File storage backend (S3) may be unavailable",
                            "Check if S3 service is running",
                            "Try using GridFS fallback if available",
                        ])
                    else:
                        diagnosis["suggestions"].extend([
                            "File may be corrupted in storage",
                            "Try re-uploading the file",
                            "Check server logs for detailed error information",
                        ])
                else:
                    diagnosis["download_successful"] = True
                    diagnosis["suggestions"].append("File download should work normally")
        except Exception as e:
            diagnosis["issues_found"].append(f"Diagnostic error: {str(e)}")
            diagnosis["suggestions"].append("Contact system administrator for assistance")
        
        return diagnosis

    async def suggest_file_alternatives(self, user_role: UserRole = UserRole.VIEWER) -> Dict[str, Any]:
        """Suggest alternative approaches when file operations fail"""
        await self.initialize(user_role)
        
        alternatives = {
            "user_role": user_role.value,
            "available_options": []
        }
        
        # Check what's available for the user
        accessible_servers = self._get_accessible_servers(user_role)
        
        if "mongodb" in accessible_servers:
            alternatives["available_options"].extend([
                {
                    "option": "List all uploaded files",
                    "description": "See what files are available in the database",
                    "command": "List all uploaded files in the database"
                },
                {
                    "option": "Search for files by pattern",
                    "description": "Find files matching a specific pattern or name",
                    "command": "Search for files containing 'customer' or 'dataset'"
                }
            ])
        
        if user_role in [UserRole.ADMIN, UserRole.DEVELOPER, UserRole.RND]:
            alternatives["available_options"].extend([
                {
                    "option": "Upload a new dataset",
                    "description": "Upload a fresh copy of your dataset",
                    "command": "Upload a new file to the database"
                },
                {
                    "option": "Clean and re-upload",
                    "description": "Remove old files and upload clean versions",
                    "command": "Clean database and upload new files"
                }
            ])
        
        # Add local file alternatives
        alternatives["local_alternatives"] = [
            {
                "option": "Use local file",
                "description": "If you have the dataset locally, you can work with it directly",
                "benefit": "Bypasses database download issues"
            },
            {
                "option": "Sample dataset",
                "description": "Use a sample or demo dataset for testing",
                "benefit": "Quick way to test your analysis workflow"
            }
        ]
        
        return alternatives

    async def troubleshoot_dataset_access(self, file_id: str = None, user_role: UserRole = UserRole.VIEWER) -> str:
        """Comprehensive troubleshooting for dataset access issues"""
        await self.initialize(user_role)
        
        troubleshoot_report = "🔧 **Dataset Access Troubleshooting Report**\n\n"
        
        # Check server connectivity
        troubleshoot_report += "**1. Server Connectivity Check:**\n"
        health_results = await self.health_check(user_role)
        
        mongodb_status = health_results.get("accessible_servers", {}).get("mongodb", {})
        if mongodb_status.get("status") == "healthy":
            troubleshoot_report += "✅ MongoDB server is accessible and healthy\n"
        else:
            troubleshoot_report += f"❌ MongoDB server issue: {mongodb_status.get('error', 'Unknown error')}\n"
            troubleshoot_report += "   → **Action:** Contact administrator to check MongoDB server\n"
            return troubleshoot_report
        
        # Check file listing capability
        troubleshoot_report += "\n**2. File Listing Check:**\n"
        try:
            list_result = await self.agent.chat(
                query="List all uploaded files in the database",
                user_id="troubleshoot",
                conversation_id="troubleshoot",
                user_role=user_role,
            )
            
            if isinstance(list_result, str) and "error" in list_result.lower():
                troubleshoot_report += "❌ Cannot list files in database\n"
                troubleshoot_report += f"   Error: {list_result[:100]}...\n"
            else:
                troubleshoot_report += "✅ File listing works\n"
                # Extract file count if possible
                import re
                count_match = re.search(r'(\d+)\s+files?', list_result.lower())
                if count_match:
                    troubleshoot_report += f"   Found {count_match.group(1)} files in database\n"
        except Exception as e:
            troubleshoot_report += f"❌ File listing failed: {str(e)}\n"
        
        # Check specific file if provided
        if file_id:
            troubleshoot_report += f"\n**3. Specific File Check (ID: {file_id}):**\n"
            
            # Check file metadata
            try:
                metadata_result = await self.agent.chat(
                    query=f"Get metadata for file with ID: {file_id}",
                    user_id="troubleshoot",
                    conversation_id="troubleshoot",
                    user_role=user_role,
                )
                
                if isinstance(metadata_result, str) and "not found" in metadata_result.lower():
                    troubleshoot_report += "❌ File metadata not found\n"
                    troubleshoot_report += "   → **Action:** Verify file ID is correct\n"
                    troubleshoot_report += "   → **Action:** Check if file was uploaded successfully\n"
                else:
                    troubleshoot_report += "✅ File metadata exists\n"
                    
                    # Try download
                    download_result = await self.agent.chat(
                        query=f"Download file with ID: {file_id}",
                        user_id="troubleshoot",
                        conversation_id="troubleshoot",
                        user_role=user_role,
                    )
                    
                    if isinstance(download_result, str) and ("error" in download_result.lower() or "failed" in download_result.lower()):
                        troubleshoot_report += "❌ File download failed\n"
                        troubleshoot_report += f"   Error details: {download_result[:200]}...\n"
                        
                        if "s3" in download_result.lower():
                            troubleshoot_report += "   → **Issue:** S3 storage backend problem\n"
                            troubleshoot_report += "   → **Action:** Check S3 service status\n"
                        elif "access denied" in download_result.lower():
                            troubleshoot_report += "   → **Issue:** Permission problem\n"
                            troubleshoot_report += "   → **Action:** Check file ownership\n"
                        else:
                            troubleshoot_report += "   → **Action:** Check server logs for details\n"
                    else:
                        troubleshoot_report += "✅ File download successful\n"
                        
            except Exception as e:
                troubleshoot_report += f"❌ File check failed: {str(e)}\n"
        
        # Provide recommendations
        troubleshoot_report += "\n**4. Recommendations:**\n"
        
        # Check if user can upload files
        if user_role in [UserRole.ADMIN, UserRole.DEVELOPER, UserRole.RND]:
            troubleshoot_report += "✓ You have upload permissions - consider re-uploading the dataset\n"
        else:
            troubleshoot_report += "⚠️ You don't have upload permissions - contact admin if file is missing\n"
        
        troubleshoot_report += "✓ Try using a local copy of the dataset as a workaround\n"
        troubleshoot_report += "✓ Check if the file exists with a different ID or name\n"
        
        # Add local file suggestion
        troubleshoot_report += "\n**5. Alternative Approaches:**\n"
        troubleshoot_report += "If the database file continues to have issues, you can:\n"
        troubleshoot_report += "- Use a local CSV file for clustering\n"
        troubleshoot_report += "- Upload a fresh copy of the dataset\n"
        troubleshoot_report += "- Try a sample dataset to test the clustering workflow\n"
        
        return troubleshoot_report
    
    async def handle_clustering_request(
        self, 
        file_id: str = None, 
        user_role: UserRole = UserRole.VIEWER,
        fallback_to_local: bool = True
    ) -> str:
        """Handle clustering requests with built-in error recovery"""
        await self.initialize(user_role)
        query = f"Perform clustering analysis on dataset with file ID: {file_id}" if file_id else "Perform clustering on the available dataset in the database"
        try:
            result = await self.agent.chat(
                query=query,
                user_id="clustering_user",
                conversation_id="clustering_session",
                user_role=user_role,
            )
            if isinstance(result, str) and ("error" not in result.lower() and "failed" not in result.lower() and "not found" not in result.lower()):
                return result
            # If there's an error, provide enhanced troubleshooting
            error_response = "❌ **Clustering failed with database file**\n\n"
            error_response += f"Original error: {result[:200]}...\n\n"
            
            # Run diagnostic
            if file_id:
                diagnostic = await self.troubleshoot_dataset_access(file_id, user_role)
                error_response += diagnostic + "\n\n"
            
            # Provide local file alternative
            if fallback_to_local:
                error_response += "🔄 **Alternative Solution:**\n"
                error_response += "Since the database file has issues, you can perform clustering using a local file:\n\n"
                error_response += "1. **If you have the mall_customers.csv file locally:**\n"
                error_response += "   - Place it in your working directory\n"
                error_response += "   - Use any clustering tool (pandas, scikit-learn, etc.)\n"
                error_response += "   - Or upload it again to the database\n\n"
                
                error_response += "2. **Sample clustering code for local file:**\n"
                error_response += "```python\n"
                error_response += "import pandas as pd\n"
                error_response += "from sklearn.cluster import KMeans\n"
                error_response += "import matplotlib.pyplot as plt\n\n"
                error_response += "# Load the dataset\n"
                error_response += "df = pd.read_csv('mall_customers.csv')\n\n"
                error_response += "# Select features for clustering (example)\n"
                error_response += "features = df[['Annual Income (k$)', 'Spending Score (1-100)']]\n\n"
                error_response += "# Perform K-means clustering\n"
                error_response += "kmeans = KMeans(n_clusters=5, random_state=42)\n"
                error_response += "clusters = kmeans.fit_predict(features)\n\n"
                error_response += "# Add cluster labels to dataframe\n"
                error_response += "df['Cluster'] = clusters\n\n"
                error_response += "# Visualize results\n"
                error_response += "plt.scatter(features.iloc[:, 0], features.iloc[:, 1], c=clusters)\n"
                error_response += "plt.xlabel('Annual Income (k$)')\n"
                error_response += "plt.ylabel('Spending Score (1-100)')\n"
                error_response += "plt.title('Customer Segmentation')\n"
                error_response += "plt.show()\n"
                error_response += "```\n\n"
                
                error_response += "3. **Upload a fresh copy to database:**\n"
                if user_role in [UserRole.ADMIN, UserRole.DEVELOPER, UserRole.RND]:
                    error_response += "   You have upload permissions - you can upload the file again\n"
                else:
                    error_response += "   Contact an admin to upload the file for you\n"
            
            return error_response
            
        except Exception as e:
            error_msg = f"❌ **Clustering request failed with exception: {str(e)}**\n\n"
            error_msg += "This might be due to:\n"
            error_msg += "- Server connectivity issues\n"
            error_msg += "- Permission problems\n"
            error_msg += "- Tool availability issues\n\n"
            error_msg += "**Suggested next steps:**\n"
            error_msg += "1. Check server health status\n"
            error_msg += "2. Verify your role permissions\n"
            error_msg += "3. Try with a local file as fallback\n"
            return error_msg
    
    async def call_tool(self, tool_name: str, args: Dict[str, Any], user_role: UserRole = UserRole.VIEWER) -> Any:
        """Direct tool invocation bypassing LLM planning."""
        await self.initialize(user_role)
        raw = await self.agent.invoke_tool(tool_name, args, user_role=user_role)
        # Normalize possible JSON-string results from adapters into dicts
        if isinstance(raw, str):
            try:
                import json as _json
                parsed = _json.loads(raw)
                return parsed
            except Exception:
                return {"success": True, "result": raw}
        return raw
    
    async def chat(self, message: str, user_id: str = "test_user", conversation_id: Optional[str] = None, user_role: Optional[UserRole] = None) -> Dict[str, Any]:
        """Simple chat wrapper used by tests; ensures initialization and returns a dict with 'response'."""
        try:
            role = user_role or self.current_role or UserRole.VIEWER
            if self.current_role is None:
                await self.initialize(role)
            
            # Create UserContext for the agent
            from agent.rbac_system import get_rbac_manager
            rbac = get_rbac_manager()
            user_context = rbac.create_user_context(user_id, role)
            
            result = await self.agent.process_message(
                message=message,
                conversation_id=conversation_id or "test-conv",
                user_context=user_context,
            )
            if isinstance(result, dict):
                return {"response": result.get("result", result), **({} if "response" in result else {})}
            return {"response": result}
        except Exception as e:
            return {"response": f"Error: {e}"}

# Utility functions
async def create_mcp_client(
    provider: str = "openai", 
    model: str = "gpt-4o-mini",
    user_role: UserRole = UserRole.VIEWER
) -> MCPClient:
    """Create and initialize MCP client with RBAC"""
    client = MCPClient(provider=provider, model=model)
    await client.initialize(user_role=user_role)
    return client