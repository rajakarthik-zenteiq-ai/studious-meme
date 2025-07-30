"""
Enhanced file upload utilities for MCP UI
Handles proper file upload with progress tracking and error handling
"""
import base64
import hashlib
import mimetypes
from typing import Dict, Any, List, Optional
import asyncio
import logging

logger = logging.getLogger(__name__)

class FileUploadHandler:
    """Handles file uploads with proper error handling and progress tracking"""
    
    def __init__(self, client):
        self.client = client
        
    async def upload_file(
        self, 
        file_content: bytes, 
        filename: str, 
        content_type: str,
        user_id: str,
        chunk_size: int = 1024 * 1024  # 1MB chunks
    ) -> Dict[str, Any]:
        """Upload a file with progress tracking"""
        
        try:
            # Validate file
            if len(file_content) == 0:
                return {"success": False, "error": "File is empty"}
            
            if len(file_content) > 50 * 1024 * 1024:  # 50MB limit
                return {"success": False, "error": "File too large (max 50MB)"}
            
            # Generate file hash for integrity
            file_hash = hashlib.md5(file_content).hexdigest()
            
            # Encode content to base64
            encoded_content = base64.b64encode(file_content).decode('utf-8')
            
            # Prepare upload request
            upload_data = {
                "filename": filename,
                "content": encoded_content,
                "content_type": content_type,
                "user_id": user_id,
                "file_size": len(file_content),
                "file_hash": file_hash,
                "metadata": {
                    "upload_source": "streamlit_ui",
                    "original_filename": filename
                }
            }
            
            # Use the MCP client to upload
            upload_query = f"""Upload file '{filename}' to database. 
            File details:
            - Size: {len(file_content)} bytes
            - Type: {content_type}
            - User: {user_id}
            - Hash: {file_hash}
            """
            
            result = await self.client.process_query(
                query=upload_query,
                user_id=user_id,
                conversation_id="file_upload",
                user_role=self.client.current_role
            )
            
            # Parse result
            if "success" in result.lower() and "error" not in result.lower():
                return {
                    "success": True,
                    "filename": filename,
                    "file_size": len(file_content),
                    "file_hash": file_hash,
                    "result": result
                }
            else:
                return {
                    "success": False,
                    "filename": filename,
                    "error": result
                }
                
        except Exception as e:
            logger.error(f"File upload error: {e}")
            return {
                "success": False,
                "filename": filename,
                "error": str(e)
            }
    
    async def upload_multiple_files(
        self,
        files: List,
        user_id: str,
        progress_callback=None
    ) -> List[Dict[str, Any]]:
        """Upload multiple files with progress tracking"""
        
        results = []
        total_files = len(files)
        
        for i, file in enumerate(files):
            try:
                # Get file info
                filename = file.name
                content_type = file.type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
                file_content = file.getvalue()
                
                # Update progress
                if progress_callback:
                    progress_callback(i, total_files, f"Uploading {filename}...")
                
                # Upload file
                result = await self.upload_file(
                    file_content=file_content,
                    filename=filename,
                    content_type=content_type,
                    user_id=user_id
                )
                
                results.append(result)
                
            except Exception as e:
                results.append({
                    "success": False,
                    "filename": getattr(file, 'name', 'unknown'),
                    "error": str(e)
                })
        
        if progress_callback:
            progress_callback(total_files, total_files, "Upload complete!")
        
        return results
    
    async def validate_uploaded_file(self, file_id: str, user_id: str) -> Dict[str, Any]:
        """Validate that an uploaded file exists and is accessible"""
        try:
            # Try to get file metadata
            query = f"Get metadata for file ID: {file_id}"
            result = await self.client.process_query(
                query=query,
                user_id=user_id,
                conversation_id="file_validation",
                user_role=self.client.current_role
            )
            
            if "not found" in result.lower():
                return {"valid": False, "error": "File not found in database"}
            
            # Try to download file (just check, don't return content)
            download_query = f"Check if file {file_id} can be downloaded"
            download_result = await self.client.process_query(
                query=download_query,
                user_id=user_id,
                conversation_id="file_validation",
                user_role=self.client.current_role
            )
            
            if "error" in download_result.lower():
                return {"valid": False, "error": "File exists but cannot be downloaded"}
            
            return {"valid": True, "metadata": result}
            
        except Exception as e:
            return {"valid": False, "error": str(e)}

def get_file_icon(filename: str) -> str:
    """Get appropriate icon for file type"""
    ext = filename.lower().split('.')[-1] if '.' in filename else ''
    
    icons = {
        'csv': '📊',
        'json': '📋',
        'txt': '📄',
        'log': '📝',
        'pdf': '📕',
        'xlsx': '📗',
        'xls': '📗',
        'png': '🖼️',
        'jpg': '🖼️',
        'jpeg': '🖼️',
        'zip': '📦',
        'tar': '📦',
        'gz': '📦'
    }
    
    return icons.get(ext, '📄')

def format_file_size(size_bytes: int) -> str:
    """Format file size in human readable format"""
    if size_bytes == 0:
        return "0B"
    
    size_names = ["B", "KB", "MB", "GB"]
    i = 0
    while size_bytes >= 1024 and i < len(size_names) - 1:
        size_bytes /= 1024.0
        i += 1
    
    return f"{size_bytes:.1f}{size_names[i]}"
