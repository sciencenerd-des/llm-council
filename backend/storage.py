"""Supabase-based storage for conversations."""

from datetime import datetime
from typing import List, Dict, Any, Optional
from supabase import create_client, Client
from .config import SUPABASE_URL, SUPABASE_KEY

# Initialize Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def create_conversation(conversation_id: str) -> Dict[str, Any]:
    """
    Create a new conversation.

    Args:
        conversation_id: Unique identifier for the conversation

    Returns:
        New conversation dict
    """
    conversation_data = {
        "id": conversation_id,
        "title": "New Conversation"
    }

    result = supabase.table("conversations").insert(conversation_data).execute()

    if result.data:
        row = result.data[0]
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "title": row["title"],
            "messages": []
        }

    raise Exception("Failed to create conversation")


def get_conversation(conversation_id: str) -> Optional[Dict[str, Any]]:
    """
    Load a conversation from storage.

    Args:
        conversation_id: Unique identifier for the conversation

    Returns:
        Conversation dict or None if not found
    """
    # Get conversation
    conv_result = supabase.table("conversations").select("*").eq("id", conversation_id).execute()

    if not conv_result.data:
        return None

    conv = conv_result.data[0]

    # Get messages for this conversation
    msg_result = supabase.table("messages").select("*").eq("conversation_id", conversation_id).order("created_at").execute()

    messages = []
    for msg in msg_result.data or []:
        if msg["role"] == "user":
            message = {
                "role": "user",
                "content": msg["content"]
            }
            if msg.get("file_info"):
                message["file"] = msg["file_info"]
        else:
            message = {
                "role": "assistant",
                "stage1": msg.get("stage1", []),
                "stage2": msg.get("stage2", []),
                "stage3": msg.get("stage3", {})
            }
        messages.append(message)

    return {
        "id": conv["id"],
        "created_at": conv["created_at"],
        "title": conv["title"],
        "messages": messages
    }


def list_conversations() -> List[Dict[str, Any]]:
    """
    List all conversations (metadata only).

    Returns:
        List of conversation metadata dicts
    """
    # Optimized: Get all conversations with message counts in a single query
    # Using Supabase's count feature with proper aggregation
    result = supabase.table("conversations").select(
        "id, created_at, title, messages(count)"
    ).order("created_at", desc=True).execute()

    conversations = []
    for conv in result.data or []:
        # Extract message count from the nested relationship
        messages_data = conv.get("messages", [])
        message_count = messages_data[0]["count"] if messages_data else 0

        conversations.append({
            "id": conv["id"],
            "created_at": conv["created_at"],
            "title": conv["title"],
            "message_count": message_count
        })

    return conversations


def add_user_message(conversation_id: str, content: str, file_info: dict = None):
    """
    Add a user message to a conversation.

    Args:
        conversation_id: Conversation identifier
        content: User message content
        file_info: Optional dict with file metadata (filename, file_type)
    """
    message_data = {
        "conversation_id": conversation_id,
        "role": "user",
        "content": content,
        "file_info": file_info
    }

    supabase.table("messages").insert(message_data).execute()


def add_assistant_message(
    conversation_id: str,
    stage1: List[Dict[str, Any]],
    stage2: List[Dict[str, Any]],
    stage3: Dict[str, Any]
):
    """
    Add an assistant message with all 3 stages to a conversation.

    Args:
        conversation_id: Conversation identifier
        stage1: List of individual model responses
        stage2: List of model rankings
        stage3: Final synthesized response
    """
    message_data = {
        "conversation_id": conversation_id,
        "role": "assistant",
        "content": None,
        "stage1": stage1,
        "stage2": stage2,
        "stage3": stage3
    }

    supabase.table("messages").insert(message_data).execute()


def update_conversation_title(conversation_id: str, title: str):
    """
    Update the title of a conversation.

    Args:
        conversation_id: Conversation identifier
        title: New title for the conversation
    """
    supabase.table("conversations").update({"title": title}).eq("id", conversation_id).execute()
