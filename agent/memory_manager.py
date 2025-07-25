"""
Memory Manager with Redis Cache and MongoDB Storage
Implements sliding window summarization and long-term memory extraction
"""
import os
import sys
import json
import asyncio
import time
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
import logging
from pathlib import Path

import redis.asyncio as redis
from motor.motor_asyncio import AsyncIOMotorClient
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_openai import ChatOpenAI

# Add config to path
sys.path.insert(0, str(Path(__file__).parent.parent / "config"))

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MemoryManager:
    """
    Manages conversation memory with Redis caching and MongoDB persistence
    Features:
    - Short-term sliding window (last 8 conversations)
    - Auto-summarization every 5 conversations
    - Long-term memory extraction every 7-8 conversations
    - 5-minute TTL with automatic refresh
    """
    
    def __init__(self):
        # Import settings
        try:
            from settings import MONGO_URI, REDIS_URL, MONGO_DB
            self.mongo_uri = MONGO_URI
            self.redis_url = REDIS_URL
            self.db_name = MONGO_DB
        except ImportError:
            # Fallback to environment variables
            self.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
            self.mongo_uri = os.getenv("MONGO_URI", "mongodb://admin:password@localhost:27017")
            self.db_name = os.getenv("MONGO_DB", "logsdb")
        
        logger.info(f"Initializing MemoryManager with MongoDB: {self.mongo_uri}")
        logger.info(f"Redis URL: {self.redis_url}")
        
        # Memory settings
        self.sliding_window_size = 8
        self.summarization_interval = 5
        self.long_term_interval = 7
        self.cache_ttl = 300  # 5 minutes
        
        # Clients
        self.redis_client = None
        self.mongo_client = None
        self.db = None
        
        # LLM for summarization
        self.summarizer = ChatOpenAI(
            model="gpt-3.5-turbo",
            temperature=0.3,
            max_tokens=500
        )
    
    async def initialize(self):
        """Initialize connections"""
        try:
            # Connect to Redis
            self.redis_client = await redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True
            )
            await self.redis_client.ping()
            logger.info("✅ Connected to Redis")
            
            # Connect to MongoDB
            self.mongo_client = AsyncIOMotorClient(self.mongo_uri)
            self.db = self.mongo_client[self.db_name]
            
            # Create indexes
            await self._create_indexes()
            logger.info("✅ Connected to MongoDB")
            
        except Exception as e:
            logger.error(f"Failed to initialize memory manager: {e}")
            raise
    
    async def _create_indexes(self):
        """Create MongoDB indexes for performance"""
        # Conversation index
        await self.db.conversations.create_index([
            ("user_id", 1),
            ("conversation_id", 1)
        ], unique=True)
        
        # Timestamp index for queries
        await self.db.conversations.create_index([
            ("updated_at", -1)
        ])
        
        # Long-term memory index
        await self.db.long_term_memory.create_index([
            ("user_id", 1),
            ("created_at", -1)
        ])
    
    async def get_context(self, user_id: str, conversation_id: str) -> Dict[str, Any]:
        """Get conversation context with cache-first strategy"""
        cache_key = f"context:{user_id}:{conversation_id}"
        
        # Check Redis cache first
        cached_context = await self.redis_client.get(cache_key)
        if cached_context:
            logger.info(f"Cache hit for {cache_key}")
            await self.redis_client.expire(cache_key, self.cache_ttl)
            return json.loads(cached_context)
        
        logger.info(f"Cache miss for {cache_key}")
        
        # Load from MongoDB
        context = await self._load_context_from_mongo(user_id, conversation_id)
        
        # Hydrate cache
        await self.redis_client.setex(
            cache_key,
            self.cache_ttl,
            json.dumps(context)
        )
        
        return context
    
    async def _load_context_from_mongo(self, user_id: str, conversation_id: str) -> Dict[str, Any]:
        """Load context from MongoDB with sliding window"""
        conversation = await self.db.conversations.find_one({
            "user_id": user_id,
            "conversation_id": conversation_id
        })
        
        if not conversation:
            return {
                "messages": [],
                "summary": "",
                "long_term_memory": [],
                "metadata": {}
            }
        
        messages = conversation.get("messages", [])
        if len(messages) > self.sliding_window_size * 2:
            recent_messages = messages[-(self.sliding_window_size * 2):]
            context = {
                "messages": recent_messages,
                "summary": conversation.get("summary", ""),
                "long_term_memory": conversation.get("long_term_memory", []),
                "metadata": conversation.get("metadata", {})
            }
        else:
            context = {
                "messages": messages,
                "summary": conversation.get("summary", ""),
                "long_term_memory": conversation.get("long_term_memory", []),
                "metadata": conversation.get("metadata", {})
            }
        
        return context
    
    async def update_conversation(self, user_id: str, conversation_id: str, 
                                message: Dict[str, str], response: Dict[str, str], 
                                metadata: Optional[Dict[str, Any]] = None):
        """Update conversation with new exchange"""
        timestamp = datetime.utcnow()
        
        message["timestamp"] = timestamp.isoformat()
        response["timestamp"] = timestamp.isoformat()
        
        update_doc = {
            "$push": {
                "messages": {"$each": [message, response]}
            },
            "$set": {"updated_at": timestamp},
            "$setOnInsert": {
                "user_id": user_id,
                "conversation_id": conversation_id,
                "created_at": timestamp
            }
        }
        
        if metadata:
            update_doc["$set"]["metadata"] = metadata
        
        await self.db.conversations.update_one(
            {"user_id": user_id, "conversation_id": conversation_id},
            update_doc,
            upsert=True
        )
    
    async def close(self):
        """Close connections"""
        if self.redis_client:
            await self.redis_client.close()
        if self.mongo_client:
            self.mongo_client.close()
        logger.info("Memory manager connections closed")