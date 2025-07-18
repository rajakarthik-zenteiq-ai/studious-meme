
async def generate_sse_stream(
    query: str,
    user_id: str,
    conversation_id: str,
    attachments: List[Dict] = None,
    provider: str = "openai",
    model: str = "gpt-4o-mini"
) -> AsyncGenerator[str, None]:
    """Generate SSE stream in OpenAI format"""
    global agent_instance
    
    if not agent_instance:
        await initialize_services()
    
    try:
        # Switch provider if needed
        if provider != agent_instance.llm_provider:
            await agent_instance.switch_llm_provider(provider)
        
        # Create unique completion ID
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
        created_timestamp = int(time.time())
        
        # Initial chunk with role
        initial_chunk = ChatStreamChunk(
            id=completion_id,
            created=created_timestamp,
            model=model,
            system_fingerprint=f"fp_{uuid.uuid4().hex[:8]}",
            choices=[{
                "index": 0,
                "delta": {"role": "assistant", "content": ""},
                "logprobs": None,
                "finish_reason": None
            }]
        )
        
        yield f"data: {initial_chunk.model_dump_json()}\n\n"
        
        # Process with agent
        response_buffer = ""
        async for chunk in agent_instance.analyze_stream(
            query=query,
            user_id=user_id,
            conversation_id=conversation_id,
            attachments=attachments
        ):
            # Create content chunk
            content_chunk = ChatStreamChunk(
                id=completion_id,
                created=created_timestamp,
                model=model,
                system_fingerprint=initial_chunk.system_fingerprint,
                choices=[{
                    "index": 0,
                    "delta": {"content": chunk},
                    "logprobs": None,
                    "finish_reason": None
                }]
            )
            
            response_buffer += chunk
            yield f"data: {content_chunk.model_dump_json()}\n\n"
        
        # Final chunk with finish reason
        final_chunk = ChatStreamChunk(
            id=completion_id,
            created=created_timestamp,
            model=model,
            system_fingerprint=initial_chunk.system_fingerprint,
            choices=[{
                "index": 0,
                "delta": {},
                "logprobs": None,
                "finish_reason": "stop"
            }]
        )
        
        yield f"data: {final_chunk.model_dump_json()}\n\n"
        yield "data: [DONE]\n\n"
        
    except Exception as e:
        logger.error(f"Stream generation error: {e}")
        error_chunk = {
            "error": {
                "message": str(e),
                "type": "server_error",
                "code": "internal_error"
            }
        }
        yield f"data: {json.dumps(error_chunk)}\n\n"
