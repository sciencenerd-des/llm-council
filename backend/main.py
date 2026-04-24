"""FastAPI backend for LLM Council."""

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from pathlib import Path
import uuid
import json
import asyncio

from . import storage
from .council import (
    run_full_council,
    generate_conversation_title,
    stage1_collect_responses,
    stage1_collect_responses_progressive,
    stage1_collect_responses_with_code,
    stage1_collect_responses_with_code_progressive,
    stage2_collect_rankings,
    stage2_collect_rankings_with_code_progressive,
    stage3_synthesize_final,
    calculate_aggregate_rankings,
    E2B_ENABLED
)
from .openrouter import close_http_client
from .csv_processor import CSVProcessor
from .config import COUNCIL_MODELS

app = FastAPI(title="LLM Council API")


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up resources on shutdown."""
    await close_http_client()


# Create output directory for plots and serve static files
Path("data/outputs").mkdir(parents=True, exist_ok=True)
app.mount("/outputs", StaticFiles(directory="data/outputs"), name="outputs")

# Enable CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5175", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreateConversationRequest(BaseModel):
    """Request to create a new conversation."""
    pass


class SendMessageRequest(BaseModel):
    """Request to send a message in a conversation."""
    content: str


class ConversationMetadata(BaseModel):
    """Conversation metadata for list view."""
    id: str
    created_at: str
    title: str
    message_count: int


class Conversation(BaseModel):
    """Full conversation with all messages."""
    id: str
    created_at: str
    title: str
    messages: List[Dict[str, Any]]


@app.get("/")
async def root():
    """Health check endpoint."""
    return {"status": "ok", "service": "LLM Council API"}


@app.get("/api/conversations", response_model=List[ConversationMetadata])
async def list_conversations():
    """List all conversations (metadata only)."""
    return storage.list_conversations()


@app.post("/api/conversations", response_model=Conversation)
async def create_conversation(request: CreateConversationRequest):
    """Create a new conversation."""
    conversation_id = str(uuid.uuid4())
    conversation = storage.create_conversation(conversation_id)
    return conversation


@app.get("/api/conversations/{conversation_id}", response_model=Conversation)
async def get_conversation(conversation_id: str):
    """Get a specific conversation with all its messages."""
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@app.post("/api/conversations/{conversation_id}/message")
async def send_message(conversation_id: str, request: SendMessageRequest):
    """
    Send a message and run the 3-stage council process.
    Returns the complete response with all stages.
    """
    # Check if conversation exists
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Check if this is the first message
    is_first_message = len(conversation["messages"]) == 0

    # Add user message
    storage.add_user_message(conversation_id, request.content)

    # If this is the first message, generate a title
    if is_first_message:
        title = await generate_conversation_title(request.content)
        storage.update_conversation_title(conversation_id, title)

    # Run the 3-stage council process
    stage1_results, stage2_results, stage3_result, metadata = await run_full_council(
        request.content
    )

    # Add assistant message with all stages
    storage.add_assistant_message(
        conversation_id,
        stage1_results,
        stage2_results,
        stage3_result
    )

    # Return the complete response with metadata
    return {
        "stage1": stage1_results,
        "stage2": stage2_results,
        "stage3": stage3_result,
        "metadata": metadata
    }


@app.post("/api/conversations/{conversation_id}/message/stream")
async def send_message_stream(conversation_id: str, request: SendMessageRequest):
    """
    Send a message and stream the 3-stage council process.
    Returns Server-Sent Events as each stage completes.
    """
    # Check if conversation exists
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Check if this is the first message
    is_first_message = len(conversation["messages"]) == 0

    async def event_generator():
        try:
            # Add user message
            storage.add_user_message(conversation_id, request.content)

            # Start title generation in parallel (don't await yet)
            title_task = None
            if is_first_message:
                title_task = asyncio.create_task(generate_conversation_title(request.content))

            # Stage 1: Collect responses
            yield f"data: {json.dumps({'type': 'stage1_start'})}\n\n"
            stage1_results = await stage1_collect_responses(request.content)
            yield f"data: {json.dumps({'type': 'stage1_complete', 'data': stage1_results})}\n\n"

            # Stage 2: Collect rankings
            yield f"data: {json.dumps({'type': 'stage2_start'})}\n\n"
            stage2_results, label_to_model = await stage2_collect_rankings(request.content, stage1_results)
            aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)
            yield f"data: {json.dumps({'type': 'stage2_complete', 'data': stage2_results, 'metadata': {'label_to_model': label_to_model, 'aggregate_rankings': aggregate_rankings}})}\n\n"

            # Stage 3: Synthesize final answer
            yield f"data: {json.dumps({'type': 'stage3_start'})}\n\n"
            stage3_result = await stage3_synthesize_final(request.content, stage1_results, stage2_results, csv_info=None)
            yield f"data: {json.dumps({'type': 'stage3_complete', 'data': stage3_result})}\n\n"

            # Wait for title generation if it was started
            if title_task:
                title = await title_task
                storage.update_conversation_title(conversation_id, title)
                yield f"data: {json.dumps({'type': 'title_complete', 'data': {'title': title}})}\n\n"

            # Save complete assistant message
            storage.add_assistant_message(
                conversation_id,
                stage1_results,
                stage2_results,
                stage3_result
            )

            # Send completion event
            yield f"data: {json.dumps({'type': 'complete'})}\n\n"

        except Exception as e:
            # Send error event
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@app.post("/api/conversations/{conversation_id}/message/with-csv/stream")
async def send_message_with_csv_stream(
    conversation_id: str,
    content: str = Form(...),
    file: Optional[UploadFile] = File(None)
):
    """Send a message with optional CSV file (streaming response with code execution)."""
    conversation = storage.get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Check if this is the first message
    is_first_message = len(conversation["messages"]) == 0

    # Process CSV - store full file for code execution
    csv_info = None
    if file and file.filename:
        try:
            csv_info = await CSVProcessor.store_full_csv(file)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    async def event_generator():
        try:
            # Store user message with file info
            file_info = {"filename": csv_info["filename"], "file_type": "csv", "row_count": csv_info["row_count"]} if csv_info else None
            storage.add_user_message(conversation_id, content, file_info=file_info)

            # Start title generation in parallel (don't await yet)
            title_task = None
            if is_first_message:
                title_task = asyncio.create_task(generate_conversation_title(content))

            if csv_info:
                # Use PROGRESSIVE code execution flow for CSV analysis
                yield f"data: {json.dumps({'type': 'stage1_start', 'mode': 'code_execution', 'e2b_enabled': E2B_ENABLED, 'model_count': len(COUNCIL_MODELS)})}\n\n"

                stage1_results = []
                async for result in stage1_collect_responses_with_code_progressive(content, csv_info):
                    # Yield each model's result immediately as it completes
                    stage1_results.append(result)
                    yield f"data: {json.dumps({'type': 'stage1_model_complete', 'data': result, 'completed_count': len(stage1_results), 'total_count': len(COUNCIL_MODELS)})}\n\n"

                yield f"data: {json.dumps({'type': 'stage1_complete', 'data': stage1_results})}\n\n"
            else:
                # Regular text-only flow (PROGRESSIVE)
                yield f"data: {json.dumps({'type': 'stage1_start', 'mode': 'text_only', 'model_count': len(COUNCIL_MODELS)})}\n\n"

                stage1_results = []
                async for result in stage1_collect_responses_progressive(content):
                    # Yield each model's result immediately as it completes
                    stage1_results.append(result)
                    yield f"data: {json.dumps({'type': 'stage1_model_complete', 'data': result, 'completed_count': len(stage1_results), 'total_count': len(COUNCIL_MODELS)})}\n\n"

                yield f"data: {json.dumps({'type': 'stage1_complete', 'data': stage1_results})}\n\n"

            # Stage 2: Collect rankings with optional code execution (PROGRESSIVE)
            yield f"data: {json.dumps({'type': 'stage2_start', 'mode': 'code_enabled' if csv_info else 'text_only', 'model_count': len(COUNCIL_MODELS)})}\n\n"

            stage2_results = []
            label_to_model = {}

            async for item in stage2_collect_rankings_with_code_progressive(content, stage1_results, csv_info):
                if item["type"] == "label_to_model":
                    label_to_model = item["data"]
                elif item["type"] == "ranking":
                    stage2_results.append(item["data"])
                    # Yield each ranking result progressively
                    yield f"data: {json.dumps({'type': 'stage2_model_complete', 'data': item['data'], 'completed_count': len(stage2_results), 'total_count': len(COUNCIL_MODELS)})}\n\n"

            aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)
            yield f"data: {json.dumps({'type': 'stage2_complete', 'data': stage2_results, 'metadata': {'label_to_model': label_to_model, 'aggregate_rankings': aggregate_rankings}})}\n\n"

            # Stage 3: Synthesize final answer (pass csv_info for code execution)
            yield f"data: {json.dumps({'type': 'stage3_start'})}\n\n"
            stage3_result = await stage3_synthesize_final(content, stage1_results, stage2_results, csv_info=csv_info)
            yield f"data: {json.dumps({'type': 'stage3_complete', 'data': stage3_result})}\n\n"

            # Wait for title generation if it was started
            if title_task:
                title = await title_task
                storage.update_conversation_title(conversation_id, title)
                yield f"data: {json.dumps({'type': 'title_complete', 'data': {'title': title}})}\n\n"

            # Save complete assistant message
            storage.add_assistant_message(
                conversation_id,
                stage1_results,
                stage2_results,
                stage3_result
            )

            # Send completion event
            yield f"data: {json.dumps({'type': 'complete'})}\n\n"

        except Exception as e:
            # Send error event
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
