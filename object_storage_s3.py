"""
Akamai Object Storage (S3-compatible) Manager
Following latest Akamai Cloud Computing documentation for boto3 integration
Reference: https://techdocs.akamai.com/cloud-computing/docs/using-the-aws-sdk-for-python-boto3-with-object-storage
"""
import boto3
from botocore.exceptions import ClientError, NoCredentialsError
import os
import io
import base64
import logging
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime

# Logging setup
logger = logging.getLogger(__name__)

class LinodeObjectStorage:
    """
    Akamai Object Storage client with comprehensive S3-compatible operations
    
    Follows Akamai's official boto3 integration patterns:
    - Uses environment variables for credentials (recommended approach)
    - Single bucket with directory prefixes for organization
    - Standard boto3 S3 client methods with proper error handling
    """
    
    def __init__(self):
        """
        Initialize Akamai Object Storage client with environment configuration
        
        Required environment variables:
        - AWS_ACCESS_KEY_ID: Your Akamai Object Storage access key
        - AWS_SECRET_ACCESS_KEY: Your Akamai Object Storage secret key
        - AWS_ENDPOINT_URL: Cluster URL (e.g., https://in-maa-1.linodeobjects.com)
        """
        # Configuration following Akamai documentation pattern
        self.linode_obj_config = {
            "aws_access_key_id": os.getenv("AWS_ACCESS_KEY_ID"),
            "aws_secret_access_key": os.getenv("AWS_SECRET_ACCESS_KEY"),
            "endpoint_url": os.getenv("AWS_ENDPOINT_URL", "https://in-maa-1.linodeobjects.com"),
        }
        
        # Validate required credentials
        if not self.linode_obj_config["aws_access_key_id"] or not self.linode_obj_config["aws_secret_access_key"]:
            raise ValueError(
                "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must be set. "
                "Generate these from Akamai Cloud Manager > Object Storage > Access Keys"
            )
        
        # Initialize S3 client using Akamai's recommended pattern
        self.client = boto3.client("s3", **self.linode_obj_config)
        self.default_bucket = "mcp"  # Single bucket with directory prefixes
        self.endpoint_url = self.linode_obj_config["endpoint_url"]
        
        # Verify bucket exists and is accessible
        self._verify_bucket_access()
    
    def _verify_bucket_access(self):
        """
        Verify that the default bucket exists and is accessible
        Uses Akamai's recommended head_bucket operation for validation
        """
        try:
            self.client.head_bucket(Bucket=self.default_bucket)
            logger.info(f"✅ Successfully connected to Akamai Object Storage bucket '{self.default_bucket}'")
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == '404':
                logger.error(f"❌ Bucket '{self.default_bucket}' does not exist")
                raise ValueError(
                    f"Bucket '{self.default_bucket}' not found. "
                    f"Please create it in Akamai Cloud Manager > Object Storage"
                )
            elif error_code == '403':
                logger.error(f"❌ Access denied to bucket '{self.default_bucket}'")
                raise ValueError(
                    f"Access denied to bucket '{self.default_bucket}'. "
                    f"Check your access key permissions"
                )
            else:
                logger.error(f"❌ Error accessing bucket '{self.default_bucket}': {e}")
                raise
        except NoCredentialsError:
            logger.error("❌ No valid credentials found")
            raise ValueError(
                "Invalid credentials. Please check AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY"
            )
    
    def list_buckets(self) -> List[str]:
        """
        List all buckets in the cluster
        Following Akamai documentation: client.list_buckets()
        """
        try:
            response = self.client.list_buckets()
            bucket_names = [bucket['Name'] for bucket in response['Buckets']]
            logger.info(f"Found {len(bucket_names)} buckets")
            return bucket_names
        except ClientError as e:
            logger.error(f"Failed to list buckets: {e}")
            return []
    
    def list_objects(self, bucket_name: str = None, prefix: str = "") -> List[Dict[str, Any]]:
        """
        List objects in a bucket with optional prefix filtering
        Following Akamai documentation: client.list_objects(Bucket='bucket-label', Prefix='object-prefix')
        
        Args:
            bucket_name: Bucket to list objects from (defaults to 'mcp')
            prefix: Optional prefix to filter objects (for pseudo-directories)
        """
        bucket = bucket_name or self.default_bucket
        
        try:
            # Use list_objects (v1) as shown in Akamai documentation
            if prefix:
                response = self.client.list_objects(Bucket=bucket, Prefix=prefix)
            else:
                response = self.client.list_objects(Bucket=bucket)
            
            objects = []
            
            if 'Contents' in response:
                for obj in response['Contents']:
                    objects.append({
                        'key': obj['Key'],
                        'size': obj['Size'],
                        'last_modified': obj['LastModified'].isoformat(),
                        'etag': obj['ETag'].strip('"')
                    })
            
            logger.info(f"Found {len(objects)} objects in bucket '{bucket}' with prefix '{prefix}'")
            return objects
            
        except ClientError as e:
            logger.error(f"Failed to list objects in bucket '{bucket}': {e}")
            return []
    
    def upload_file(self, file_path: str, bucket_name: str = None, object_name: str = None) -> bool:
        """
        Upload a file as an object to the bucket
        Following Akamai documentation: client.upload_file(Filename='file.txt', Bucket='bucket', Key='object-name')
        
        Args:
            file_path: Path to the file to upload
            bucket_name: Target bucket (defaults to 'mcp')
            object_name: Name for the object (defaults to filename)
        """
        bucket = bucket_name or self.default_bucket
        
        if object_name is None:
            object_name = os.path.basename(file_path)
        
        try:
            # Following Akamai's upload_file pattern
            self.client.upload_file(Filename=file_path, Bucket=bucket, Key=object_name)
            logger.info(f"✅ Uploaded {file_path} to {bucket}/{object_name}")
            return True
        except (ClientError, FileNotFoundError) as e:
            logger.error(f"❌ Failed to upload {file_path} to {bucket}/{object_name}: {e}")
            return False
    
    def upload_bytes(self, key: str, data: bytes, bucket_name: str = None) -> str:
        """
        Upload bytes data as an object to the bucket
        Uses put_object for in-memory data following boto3 best practices
        
        Args:
            key: Object key/name (including any prefix like 'datasets/file.csv')
            data: Bytes data to upload
            bucket_name: Target bucket (defaults to 'mcp')
            
        Returns:
            Object URL if successful
        """
        bucket = bucket_name or self.default_bucket
        
        try:
            # Use put_object for bytes data
            self.client.put_object(Bucket=bucket, Key=key, Body=data)
            
            # Construct object URL
            endpoint_clean = self.endpoint_url.replace('https://', '').replace('http://', '')
            url = f"https://{endpoint_clean}/{bucket}/{key}"
            
            logger.info(f"✅ Successfully uploaded {len(data)} bytes to '{key}' in bucket '{bucket}'")
            return url
        except ClientError as e:
            logger.error(f"❌ Failed to upload object '{key}' to bucket '{bucket}': {e}")
            raise
    
    def upload_base64(self, base64_data: str, object_name: str, bucket_name: str = None,
                     content_type: str = "application/octet-stream", metadata: Dict[str, str] = None) -> bool:
        """
        Upload base64-encoded data to bucket
        Convenience method that decodes base64 and uploads as bytes
        """
        try:
            # Decode base64 data
            data = base64.b64decode(base64_data)
            # Note: upload_bytes signature is (key, data, bucket_name)
            result = self.upload_bytes(object_name, data, bucket_name)
            return bool(result)  # Convert URL to boolean
        except Exception as e:
            logger.error(f"❌ Failed to upload base64 data to {object_name}: {e}")
            return False
    
    def download_file(self, object_name: str, file_path: str, bucket_name: str = None) -> bool:
        """
        Download an object to a file
        Following Akamai documentation: client.download_file(Bucket='bucket', Key='object-name', Filename='file.txt')
        
        Args:
            object_name: Name/key of the object to download
            file_path: Local path where file should be saved
            bucket_name: Source bucket (defaults to 'mcp')
        """
        bucket = bucket_name or self.default_bucket
        
        try:
            # Following Akamai's download_file pattern
            self.client.download_file(Bucket=bucket, Key=object_name, Filename=file_path)
            logger.info(f"✅ Downloaded {bucket}/{object_name} to {file_path}")
            return True
        except ClientError as e:
            logger.error(f"❌ Failed to download {bucket}/{object_name} to {file_path}: {e}")
            return False
    
    def download_bytes(self, object_name: str, bucket_name: str = None) -> Optional[bytes]:
        """
        Download an object as bytes data
        Uses get_object for in-memory access following boto3 best practices
        
        Args:
            object_name: Name/key of the object to download (including any prefix)
            bucket_name: Source bucket (defaults to 'mcp')
            
        Returns:
            Bytes data if successful, None if failed
        """
        bucket = bucket_name or self.default_bucket
        
        try:
            # Use get_object to retrieve object data
            response = self.client.get_object(Bucket=bucket, Key=object_name)
            data = response['Body'].read()
            
            logger.info(f"✅ Downloaded {len(data)} bytes from '{object_name}' in bucket '{bucket}'")
            return data
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == 'NoSuchKey':
                logger.warning(f"⚠️ Object '{object_name}' not found in bucket '{bucket}'")
            else:
                logger.error(f"❌ Failed to download bytes from {bucket}/{object_name}: {e}")
            return None
    
    def download_base64(self, object_name: str, bucket_name: str = None) -> Optional[str]:
        """
        Download object as base64-encoded string
        Convenience method that downloads bytes and encodes as base64
        """
        data = self.download_bytes(object_name, bucket_name)
        if data:
            return base64.b64encode(data).decode('utf-8')
        return None
    
    def delete_object(self, key: str, bucket_name: str = None) -> bool:
        """
        Delete an object from a bucket
        Following Akamai documentation: client.delete_object(Bucket='bucket', Key='object-name')
        
        Args:
            key: Object key/name to delete (including any prefix)
            bucket_name: Target bucket (defaults to 'mcp')
        """
        bucket = bucket_name or self.default_bucket
        
        try:
            # Following Akamai's delete_object pattern
            self.client.delete_object(Bucket=bucket, Key=key)
            logger.info(f"✅ Successfully deleted object '{key}' from bucket '{bucket}'")
            return True
        except ClientError as e:
            logger.error(f"❌ Failed to delete object '{key}' from bucket '{bucket}': {e}")
            return False
    
    def object_exists(self, object_name: str, bucket_name: str = None) -> bool:
        """Check if object exists in bucket"""
        bucket = bucket_name or self.default_bucket
        
        try:
            self.client.head_object(Bucket=bucket, Key=object_name)
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                return False
            logger.error(f"Error checking object {bucket}/{object_name}: {e}")
            return False
    
    def get_object_metadata(self, object_name: str, bucket_name: str = None) -> Optional[Dict[str, Any]]:
        """Get object metadata"""
        bucket = bucket_name or self.default_bucket
        
        try:
            response = self.client.head_object(Bucket=bucket, Key=object_name)
            return {
                'size': response['ContentLength'],
                'last_modified': response['LastModified'].isoformat(),
                'content_type': response.get('ContentType', 'unknown'),
                'etag': response['ETag'].strip('"'),
                'metadata': response.get('Metadata', {})
            }
        except ClientError as e:
            logger.error(f"Failed to get metadata for {bucket}/{object_name}: {e}")
            return None
    
    def copy_object(self, source_object: str, dest_object: str, 
                   source_bucket: str = None, dest_bucket: str = None) -> bool:
        """Copy object from one location to another"""
        src_bucket = source_bucket or self.default_bucket
        dst_bucket = dest_bucket or self.default_bucket
        
        try:
            copy_source = {'Bucket': src_bucket, 'Key': source_object}
            self.client.copy_object(CopySource=copy_source, Bucket=dst_bucket, Key=dest_object)
            logger.info(f"Copied {src_bucket}/{source_object} to {dst_bucket}/{dest_object}")
            return True
        except ClientError as e:
            logger.error(f"Failed to copy object: {e}")
            return False
    
    def generate_presigned_url(self, object_name: str, bucket_name: str = None, 
                              expiration: int = 3600, method: str = 'get_object') -> Optional[str]:
        """Generate a presigned URL for object access"""
        bucket = bucket_name or self.default_bucket
        
        try:
            url = self.client.generate_presigned_url(
                method,
                Params={'Bucket': bucket, 'Key': object_name},
                ExpiresIn=expiration
            )
            return url
        except ClientError as e:
            logger.error(f"Failed to generate presigned URL for {bucket}/{object_name}: {e}")
            return None
    
    def health_check(self) -> Dict[str, Any]:
        """
        Check Akamai Object Storage health and connectivity
        
        Performs comprehensive tests:
        1. List buckets (connection test)
        2. Upload small test object
        3. Download and verify test object
        4. Cleanup test object
        """
        try:
            # Test 1: Connection and bucket access
            buckets = self.list_buckets()
            bucket_test_passed = len(buckets) > 0
            
            # Test 2: Object operations with small test file
            test_key = f"health_check_{datetime.now().timestamp()}"
            test_data = b"Akamai Object Storage health check test"
            
            # Upload test
            upload_success = False
            try:
                upload_result = self.upload_bytes(test_key, test_data)
                upload_success = bool(upload_result)
            except Exception as e:
                logger.warning(f"Upload test failed: {e}")
            
            # Download test
            download_success = False
            if upload_success:
                try:
                    downloaded = self.download_bytes(test_key)
                    download_success = (downloaded == test_data)
                except Exception as e:
                    logger.warning(f"Download test failed: {e}")
            
            # Cleanup test object
            if upload_success:
                try:
                    self.delete_object(test_key)
                except Exception as e:
                    logger.warning(f"Cleanup failed: {e}")
            
            # Determine overall status
            all_tests_passed = bucket_test_passed and upload_success and download_success
            status = "healthy" if all_tests_passed else "degraded"
            
            return {
                "status": status,
                "connection_test": bucket_test_passed,
                "buckets_found": len(buckets),
                "upload_test": upload_success,
                "download_test": download_success,
                "endpoint": self.endpoint_url,
                "default_bucket": self.default_bucket,
                "timestamp": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return {
                "status": "unhealthy",
                "error": str(e),
                "endpoint": self.endpoint_url,
                "timestamp": datetime.now().isoformat()
            }

# Global instance (lazy-loaded)
_storage_instance = None

def get_storage() -> LinodeObjectStorage:
    """Get or create the global storage instance"""
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = LinodeObjectStorage()
    return _storage_instance

# Legacy compatibility functions
def list_s3_buckets() -> List[str]:
    """List all S3 buckets (legacy compatibility)"""
    return get_storage().list_buckets()

def upload_file_to_s3(bucket_name: str, file_path: str, object_name: str = None) -> bool:
    """Upload a file to S3 bucket (legacy compatibility)"""
    return get_storage().upload_file(file_path, bucket_name, object_name)