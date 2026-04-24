---
date: 2025-12-27T00:27:30+0530
researcher: Claude Code
git_commit: 92e1fccb1bdcf1bab7221aa9ed90f9dc72529131
branch: master
repository: llm-council
topic: "File Upload Integration Points for PDF, CSV, Excel, Docs, PPT, and Images"
tags: [research, codebase, file-upload, frontend, backend, api]
status: complete
last_updated: 2025-12-27
last_updated_by: Claude Code
---

# Research: File Upload Integration Points

**Date**: 2025-12-27T00:27:30+0530
**Researcher**: Claude Code
**Git Commit**: 92e1fccb1bdcf1bab7221aa9ed90f9dc72529131
**Branch**: master
**Repository**: llm-council

## Research Question
How to add file upload capabilities (PDF, CSV, Excel, Docs, PPT, images) to the LLM Council chatbot? What are the current structures and integration points?

## Summary

The LLM Council codebase currently has **no file upload functionality**. The system is designed for text-only interactions. To add file uploads, modifications are needed at:

1. **Frontend**: ChatInterface.jsx input area needs file input UI
2. **Backend API**: main.py needs new endpoints or modified endpoints accepting multipart/form-data
3. **Message Processing**: council.py needs to accept file content in prompts
4. **Storage**: storage.py needs to handle file metadata and possibly file storage

## Detailed Findings

### 1. Frontend Chat Interface Structure

**Key File**: `/Users/biswajitmondal/Developer/llm-council/frontend/src/components/ChatInterface.jsx`

**Current Input Structure (Lines 123-142)**:
- Single textarea with 3 rows, resizable
- Send button
- Form only visible when `messages.length === 0` (disappears after first message)
- No file input elements exist

**Current State Management**:
- `input`: String state for textarea value
- No file-related state

**Input Form HTML**:
```jsx
<form className="input-form" onSubmit={handleSubmit}>
  <textarea className="message-input" ... />
  <button type="submit" className="send-button">Send</button>
</form>
```

**Integration Point**: Add `<input type="file">` or drag-drop zone alongside/above textarea.

### 2. Backend API Structure

**Key File**: `/Users/biswajitmondal/Developer/llm-council/backend/main.py`

**Current Message Endpoint**:
```python
class SendMessageRequest(BaseModel):
    content: str  # Text only

@app.post("/api/conversations/{conversation_id}/message")
async def send_message(conversation_id: str, request: SendMessageRequest):
    ...
```

**No File Handling Imports**:
- No `UploadFile` from fastapi
- No `File` from fastapi
- No multipart form handling

**CORS Configuration (Line 18-24)**:
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**Integration Points**:
1. Add new file upload endpoint(s)
2. Or modify existing endpoints to accept `multipart/form-data`
3. Import `UploadFile`, `File` from FastAPI

### 3. Message Processing Flow

**Key File**: `/Users/biswajitmondal/Developer/llm-council/backend/council.py`

**Current Flow**:
```
User Query (text string)
    ↓
Stage 1: query_models_parallel(COUNCIL_MODELS, [{"role": "user", "content": query}])
    ↓
Stage 2: ranking_prompt built from query + stage1 responses
    ↓
Stage 3: chairman_prompt built from query + all prior stages
```

**Message Structure Passed to LLMs**:
```python
messages = [{"role": "user", "content": user_query}]  # Text only
```

**Integration Point**: OpenRouter API supports multimodal content. The message format can be extended:
```python
messages = [{
    "role": "user",
    "content": [
        {"type": "text", "text": "user query"},
        {"type": "image_url", "image_url": {"url": "base64 or URL"}}
    ]
}]
```

### 4. Storage Structure

**Key File**: `/Users/biswajitmondal/Developer/llm-council/backend/storage.py`

**Current Message Format**:
```python
# User message
{"role": "user", "content": "text string"}

# Assistant message
{"role": "assistant", "stage1": [...], "stage2": [...], "stage3": {...}}
```

**Storage Location**: `data/conversations/{uuid}.json`

**Integration Points**:
1. Add file storage directory: `data/uploads/` or similar
2. Extend message format to include file references
3. Consider file cleanup on conversation deletion

### 5. API Client Structure

**Key File**: `/Users/biswajitmondal/Developer/llm-council/frontend/src/api.js`

**Current Send Methods**:
```javascript
// Non-streaming
export async function sendMessage(conversationId, content) {
  const response = await fetch(`${API_BASE}/api/conversations/${conversationId}/message`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ content }),
  });
}

// Streaming
export async function sendMessageStream(conversationId, content, onEvent) {
  const response = await fetch(`${API_BASE}/api/conversations/${conversationId}/message/stream`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ content }),
  });
}
```

**Integration Point**: Change to `FormData` for file uploads:
```javascript
const formData = new FormData();
formData.append('content', messageText);
formData.append('files', fileObject);
```

## Code References

### Frontend
- `frontend/src/components/ChatInterface.jsx:123-142` - Input form area
- `frontend/src/components/ChatInterface.jsx:13-14` - State declarations
- `frontend/src/components/ChatInterface.jsx:24-30` - Submit handler
- `frontend/src/api.js:52-67` - sendMessage function
- `frontend/src/api.js:76-114` - sendMessageStream function
- `frontend/src/App.jsx:60-182` - handleSendMessage orchestration

### Backend
- `backend/main.py:32-34` - SendMessageRequest model
- `backend/main.py:82-123` - send_message endpoint
- `backend/main.py:126-194` - send_message_stream endpoint
- `backend/council.py:8-32` - stage1_collect_responses
- `backend/council.py:35-112` - stage2_collect_rankings
- `backend/council.py:115-174` - stage3_synthesize_final
- `backend/storage.py:110-127` - add_user_message
- `backend/openrouter.py:8-53` - query_model

### Configuration
- `backend/config.py:12-17` - COUNCIL_MODELS list
- `backend/config.py:20` - CHAIRMAN_MODEL
- `backend/config.py:26` - DATA_DIR

## Architecture Documentation

### Current Data Flow
```
Frontend → POST /api/conversations/{id}/message/stream
    ↓
main.py: add_user_message(content: str)
    ↓
council.py: run_full_council(user_query: str)
    ↓
openrouter.py: query_model(model, messages: [{role, content}])
    ↓
OpenRouter API → LLM responses
    ↓
Stream events back to frontend via SSE
```

### Proposed Data Flow with Files
```
Frontend → POST /api/conversations/{id}/message (multipart/form-data)
    ↓
main.py: receive files + text content
    ↓
File Processing Layer (NEW):
    - PDF: Extract text via PyPDF2/pdfplumber
    - CSV/Excel: Parse via pandas
    - Docs/PPT: Extract via python-docx/python-pptx
    - Images: Convert to base64 or store URL
    ↓
council.py: run_full_council(user_query, file_contents)
    ↓
openrouter.py: query_model(model, multimodal_messages)
    ↓
OpenRouter API → LLM responses
```

## File Type Support Requirements

| File Type | Extensions | Processing Library | Content Format |
|-----------|------------|-------------------|----------------|
| PDF | .pdf | PyPDF2, pdfplumber | Extracted text |
| CSV | .csv | pandas | Table as text/markdown |
| Excel | .xlsx, .xls | pandas, openpyxl | Table as text/markdown |
| Word | .docx, .doc | python-docx | Extracted text |
| PowerPoint | .pptx, .ppt | python-pptx | Slide text content |
| Images | .png, .jpg, .gif | base64 | Multimodal message |

## Key Considerations

1. **Multimodal Support**: OpenRouter supports multimodal content for compatible models (GPT-4V, Claude 3, Gemini Pro Vision). Not all council models may support images.

2. **File Size Limits**: Consider implementing max file size (e.g., 10MB) and total upload limits.

3. **Text Extraction**: For documents, extracted text should be prepended to user query or included as separate context.

4. **Image Handling**: Two approaches:
   - Base64 encoding (inline, simpler, larger payload)
   - File storage + URL reference (requires accessible URL or presigned URLs)

5. **Input Persistence**: Currently input form disappears after first message. File upload UI should be available for follow-up messages.

6. **Streaming Compatibility**: File uploads should work with the existing SSE streaming architecture.

## Open Questions

1. Should files be stored permanently or temporarily for the session?
2. Should file content be stored in conversation JSON or just referenced?
3. Which models in the council support multimodal (image) inputs?
4. Should there be a separate file preview/management UI?
5. How should file content be presented in the council prompts?
6. Should there be file type restrictions based on model capabilities?
