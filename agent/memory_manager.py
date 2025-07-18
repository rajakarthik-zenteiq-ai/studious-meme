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
    - Short-term sliding window (last 4 conversations)
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
        self.sliding_window_size = 4
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
        """
        Get conversation context with cache-first strategy
        """
        cache_key = f"context:{user_id}:{conversation_id}"
        
        # Check Redis cache first
        cached_context = await self.redis_client.get(cache_key)
        if cached_context:
            logger.info(f"Cache hit for {cache_key}")
            # Refresh TTL
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
        # Get conversation
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
        
        # Apply sliding window
        messages = conversation.get("messages", [])
        if len(messages) > self.sliding_window_size * 2:  # Keep last N exchanges
            # Keep summary and recent messages
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
    
    async def update_conversation(
        self,
        user_id: str,
        conversation_id: str,
        message: Dict[str, str],
        response: Dict[str, str],
        metadata: Optional[Dict[str, Any]] = None
    ):
        """Update conversation with new exchange"""
        timestamp = datetime.utcnow()
        
        # Add timestamps to messages
        message["timestamp"] = timestamp.isoformat()
        response["timestamp"] = timestamp.isoformat()
        
        # Update MongoDB
        update_doc = {
            "$push": {
                "messages": {
                    "$each": [message, response]
                }
            },
            "$set": {
                "updated_at": timestamp
            },
            "$setOnInsert": {
                "user_id": user_id,
                "conversation_id": conversation_id,
                "created_at": timestamp
            }
        }
        
        if metadata:
            update_doc["$set"]["metadata"] = metadata
        
        result = await self.db.conversations.update_one(
            {
                "user_id": user_id,
                "conversation_id": conversation_id
            },
            update_doc,
            upsert=True
        )
        
        # Get updated conversation
        conversation = await self.db.conversations.find_one({
            "user_id": user_id,
            "conversation_id": conversation_id
        })
        
        # Check if summarization needed
        message_count = len(conversation.get("messages", []))
        if message_count > 0 and message_count % (self.summarization_interval * 2) == 0:
            await self._trigger_summarization(user_id, conversation_id, conversation)
        
        # Check if long-term extraction needed
        if message_count > 0 and message_count % (self.long_term_interval * 2) == 0:
            await self._extract_long_term_memory(user_id, conversation_id, conversation)
        
        # Update Redis cache
        cache_key = f"context:{user_id}:{conversation_id}"
        context = await self._load_context_from_mongo(user_id, conversation_id)
        await self.redis_client.setex(
            cache_key,
            self.cache_ttl,
            json.dumps(context)
        )
    
    async def _trigger_summarization(
        self,
        user_id: str,
        conversation_id: str,
        conversation: Dict[str, Any]
    ):
        """Trigger LLM summarization of older messages"""
        logger.info(f"Triggering summarization for {conversation_id}")
        
        messages = conversation.get("messages", [])
        if len(messages) <= self.sliding_window_size * 2:
            return
        
        # Get messages to summarize (older than sliding window)
        messages_to_summarize = messages[:-(self.sliding_window_size * 2)]
        
        # Create summarization prompt
        conversation_text = "\n".join([
            f"{msg['role']}: {msg['content']}"
            for msg in messages_to_summarize
        ])
        
        prompt = f"""Summarize the following conversation, focusing on:
1. Key topics discussed
2. Important decisions or conclusions
3. User preferences or requirements
4. Any action items or next steps

Conversation:
{conversation_text}

Provide a concise summary in 2-3 paragraphs."""
        
        try:
            # Generate summary
            response = await self.summarizer.ainvoke([
                SystemMessage(content="You are a conversation summarizer."),
                HumanMessage(content=prompt)
            ])
            
            summary = response.content
            
            # Update conversation with summary
            await self.db.conversations.update_one(
                {
                    "user_id": user_id,
                    "conversation_id": conversation_id
                },
                {
                    "$set": {
                        "summary": summary,
                        "summarized_at": datetime.utcnow()
                    }
                }
            )
            
            logger.info(f"Summary updated for {conversation_id}")
            
        except Exception as e:
            logger.error(f"Summarization failed: {e}")
    
    async def _extract_long_term_memory(
        self,
        user_id: str,
        conversation_id: str,
        conversation: Dict[str, Any]
    ):
        """Extract important points for long-term memory"""
        logger.info(f"Extracting long-term memory for {conversation_id}")
        
        messages = conversation.get("messages", [])
        recent_messages = messages[-(self.long_term_interval * 2):]
        
        # Create extraction prompt
        conversation_text = "\n".join([
            f"{msg['role']}: {msg['content']}"
            for msg in recent_messages
        ])
        
        prompt = f"""From the following conversation, extract:
1. Important facts about the user
2. User preferences and requirements
3. Key decisions or agreements
4. Significant topics or themes

Focus only on information that would be valuable to remember long-term.

Conversation:
{conversation_text}

Return as a JSON list of important points, each with a category and description."""
        
        try:
            # Extract long-term memory
            response = await self.summarizer.ainvoke([
                SystemMessage(content="You are a memory extraction specialist. Return valid JSON only."),
                HumanMessage(content=prompt)
            ])
            
            # Parse extracted points
            try:
                memory_points = json.loads(response.content)
            except:
                memory_points = [{"category": "general", "description": response.content}]
            
            # Store in long-term memory collection
            await self.db.long_term_memory.insert_one({
                "user_id": user_id,
                "conversation_id": conversation_id,
                "memory_points": memory_points,
                "created_at": datetime.utcnow()
            })
            
            # Update conversation with memory reference
            await self.db.conversations.update_one(
                {
                    "user_id": user_id,
                    "conversation_id": conversation_id
                },
                {
                    "$push": {
                        "long_term_memory": {
                            "$each": memory_points
                        }
                    }
                }
            )
            
            logger.info(f"Long-term memory extracted for {conversation_id}")
            
        except Exception as e:
            logger.error(f"Long-term memory extraction failed: {e}")
    
    async def get_conversation(
        self,
        conversation_id: str,
        limit: int = 50,
        offset: int = 0,
        order: str = "desc"
    ) -> Dict[str, Any]:
        """Get conversation with pagination"""
        conversation = await self.db.conversations.find_one({
            "conversation_id": conversation_id
        })
        
        if not conversation:
            raise ValueError(f"Conversation {conversation_id} not found")
        
        messages = conversation.get("messages", [])
        total_messages = len(messages)
        
        # Apply pagination
        if order == "desc":
            messages = messages[::-1]  # Reverse for descending order
        
        paginated_messages = messages[offset:offset + limit]
        
        # Restore order for desc
        if order == "desc":
            paginated_messages = paginated_messages[::-1]
        
        return {
            "conversation_id": conversation_id,
            "user_id": conversation["user_id"],
            "messages": paginated_messages,
            "summary": conversation.get("summary", ""),
            "total_messages": total_messages,
            "has_more": (offset + limit) < total_messages,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "next_offset": offset + limit if (offset + limit) < total_messages else None
            }
        }
    
    async def clear_cache(self, user_id: str, conversation_id: str):
        """Manually clear cache for a conversation"""
        cache_key = f"context:{user_id}:{conversation_id}"
        await self.redis_client.delete(cache_key)
        logger.info(f"Cache cleared for {cache_key}")
    
    async def get_user_stats(self, user_id: str) -> Dict[str, Any]:
        """Get user statistics"""
        # Count conversations
        conversation_count = await self.db.conversations.count_documents({
            "user_id": user_id
        })
        
        # Count total messages
        pipeline = [
            {"$match": {"user_id": user_id}},
            {"$project": {"message_count": {"$size": "$messages"}}},
            {"$group": {"_id": None, "total": {"$sum": "$message_count"}}}
        ]
        
        result = await self.db.conversations.aggregate(pipeline).to_list(1)
        total_messages = result[0]["total"] if result else 0
        
        # Count long-term memories
        memory_count = await self.db.long_term_memory.count_documents({
            "user_id": user_id
        })
        
        return {
            "user_id": user_id,
            "conversation_count": conversation_count,
            "total_messages": total_messages,
            "memory_count": memory_count
        }
    
    async def close(self):
        """Close connections"""
        if self.redis_client:
            await self.redis_client.close()
        if self.mongo_client:
            self.mongo_client.close()
        logger.info("Memory manager connections closed")