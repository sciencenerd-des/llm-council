# CSV Upload Integration Implementation Plan

## Overview

Add CSV file upload capability to the LLM Council chatbot. Users can upload CSV files and have the data content included in council deliberations for analysis.

## Current State Analysis

The LLM Council codebase currently has **no file upload functionality**:

- **Frontend**: Single textarea input with no file handling (`ChatInterface.jsx:123-142`)
- **Backend API**: Endpoints accept only JSON with `content: str` field (`main.py:32-34`)
- **Message Processing**: Council stages pass text-only messages (`council.py`)
- **Storage**: JSON files store text messages only (`storage.py`)

## Desired End State

Users can:
1. Attach a CSV file via a file input button in the chat interface
2. See the attached filename before sending
3. Send text messages with the CSV file
4. Have CSV data converted to markdown table and included in council deliberations
5. See file reference in conversation history

### Verification:
- Upload a CSV and ask for analysis - council should discuss the data
- CSV content appears as markdown table in council context
- Files work with streaming responses

## What We're NOT Doing

- Other file types (PDF, Excel, Word, PPT, images)
- Drag-and-drop upload
- Multiple file upload
- File storage persistence
- File preview rendering

## Implementation Approach

4 phases:
1. Backend CSV processing + API endpoint
2. Frontend API client updates
3. Frontend UI (file input button)
4. Display file info in messages

---

## Phase 1: Backend CSV Processing & API

### Overview
Add CSV processing and new API endpoint accepting multipart/form-data.

### Changes Required:

#### 1. Install Dependencies
**File**: `backend/requirements.txt` (create)

```text
fastapi
uvicorn
httpx
python-dotenv
sse-starlette
pandas>=2.0.0
python-multipart>=0.0.6
tabulate>=0.9.0
```

#### 2. Create CSV Processor Module
**File**: `backend/csv_processor.py` (new file)

```python
"""CSV processing utilities."""

import io
import pandas as pd
from fastapi import UploadFile


class CSVProcessor:
    """Handles CSV file processing."""

    MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
    MAX_ROWS = 100  # Limit rows to prevent context overflow

    @classmethod
    async def process_csv(cls, file: UploadFile) -> dict:
        """
        Process uploaded CSV file and convert to markdown table.

        Returns:
            dict with keys:
                - content: CSV data as markdown table
                - filename: original filename
                - row_count: total rows in file
                - truncated: whether data was truncated
        """
        content = await file.read()

        if len(content) > cls.MAX_FILE_SIZE:
            raise ValueError(f"File size exceeds {cls.MAX_FILE_SIZE // (1024*1024)}MB limit")

        filename = file.filename or "data.csv"

        if not filename.lower().endswith('.csv'):
            raise ValueError("Only CSV files are supported")

        # Parse CSV
        try:
            df = pd.read_csv(io.BytesIO(content))
        except Exception as e:
            raise ValueError(f"Failed to parse CSV: {str(e)}")

        total_rows = len(df)
        truncated = False

        # Truncate if too many rows
        if total_rows > cls.MAX_ROWS:
            df = df.head(cls.MAX_ROWS)
            truncated = True

        # Convert to markdown table
        markdown = df.to_markdown(index=False)

        if truncated:
            markdown += f"\n\n*[Showing first {cls.MAX_ROWS} rows of {total_rows} total]*"

        return {
            "content": markdown,
            "filename": filename,
            "row_count": total_rows,
            "truncated": truncated
        }
```

#### 3. Update Main API
**File**: `backend/main.py`

Add imports at top:
```python
from fastapi import UploadFile, File, Form
from typing import Optional
from .csv_processor import CSVProcessor
```

Add new endpoint after existing message endpoints:
```python
@app.post("/api/conversations/{conversation_id}/message/with-csv")
async def send_message_with_csv(
    conversation_id: str,
    content: str = Form(...),
    file: Optional[UploadFile] = File(None)
):
    """Send a message with optional CSV file attachment."""
    conversation = storage.get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Process CSV if provided
    csv_data = None
    if file and file.filename:
        try:
            csv_data = await CSVProcessor.process_csv(file)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # Build user query with CSV context
    user_query = content
    if csv_data:
        user_query = f"{content}\n\n[Attached CSV: {csv_data['filename']}]\n{csv_data['content']}"

    # Store user message with file info
    file_info = {"filename": csv_data["filename"], "file_type": "csv"} if csv_data else None
    storage.add_user_message(conversation_id, content, file_info=file_info)

    # Run council process
    result = await run_full_council(user_query)

    # Store and return result
    storage.add_assistant_message(conversation_id, result)

    return {
        "stage1": result["stage1"],
        "stage2": result["stage2"],
        "stage3": result["stage3"],
        "metadata": result.get("metadata", {})
    }


@app.post("/api/conversations/{conversation_id}/message/with-csv/stream")
async def send_message_with_csv_stream(
    conversation_id: str,
    content: str = Form(...),
    file: Optional[UploadFile] = File(None)
):
    """Send a message with optional CSV file (streaming response)."""
    conversation = storage.get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Process CSV if provided
    csv_data = None
    if file and file.filename:
        try:
            csv_data = await CSVProcessor.process_csv(file)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # Build user query with CSV context
    user_query = content
    if csv_data:
        user_query = f"{content}\n\n[Attached CSV: {csv_data['filename']}]\n{csv_data['content']}"

    # Store user message
    file_info = {"filename": csv_data["filename"], "file_type": "csv"} if csv_data else None
    storage.add_user_message(conversation_id, content, file_info=file_info)

    async def event_generator():
        async for event in run_full_council_stream(user_query):
            yield event

    return EventSourceResponse(event_generator())
```

#### 4. Update Storage
**File**: `backend/storage.py`

Update `add_user_message` signature to accept file info:
```python
def add_user_message(conversation_id: str, content: str, file_info: dict = None):
    """Add a user message to the conversation."""
    conversation = get_conversation(conversation_id)
    if not conversation:
        return None

    message = {
        "role": "user",
        "content": content
    }

    if file_info:
        message["file"] = file_info

    conversation["messages"].append(message)
    _save_conversation(conversation)
    return conversation
```

### Success Criteria:

#### Automated Verification:
- [x] Install dependencies: `pip install pandas python-multipart tabulate`
- [x] Backend starts: `python3 -m backend.main`
- [x] No syntax errors: `python3 -m py_compile backend/csv_processor.py`

#### Manual Verification:
- [ ] Upload CSV via curl and verify markdown table in response

---

## Phase 2: Frontend API Client

### Overview
Add function to send messages with CSV files using FormData.

### Changes Required:

**File**: `frontend/src/api.js`

Add new function:
```javascript
/**
 * Send a message with optional CSV file (streaming)
 */
export async function sendMessageWithCSVStream(conversationId, content, file = null, onEvent) {
  const formData = new FormData();
  formData.append('content', content);
  if (file) {
    formData.append('file', file);
  }

  const response = await fetch(`${API_BASE}/api/conversations/${conversationId}/message/with-csv/stream`, {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
    throw new Error(error.detail || 'Failed to send message');
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    const text = decoder.decode(value);
    const lines = text.split('\n');

    for (const line of lines) {
      if (line.startsWith('data: ')) {
        const data = line.slice(6);
        if (data === '[DONE]') continue;

        try {
          const event = JSON.parse(data);
          onEvent(event);
        } catch (e) {
          console.warn('Failed to parse SSE event:', data);
        }
      }
    }
  }
}
```

### Success Criteria:

#### Automated Verification:
- [x] No syntax errors: `cd frontend && npm run build`

---

## Phase 3: Frontend UI

### Overview
Add CSV file input button to chat interface.

### Changes Required:

#### 1. Update Chat Interface
**File**: `frontend/src/components/ChatInterface.jsx`

Add imports and state:
```jsx
import { useState, useRef } from 'react';

// Inside component:
const [selectedFile, setSelectedFile] = useState(null);
const fileInputRef = useRef(null);
```

Add handlers:
```jsx
const handleFileChange = (e) => {
  const file = e.target.files[0];
  if (file) {
    if (!file.name.toLowerCase().endsWith('.csv')) {
      alert('Only CSV files are supported.');
      return;
    }
    if (file.size > 5 * 1024 * 1024) {
      alert('File size exceeds 5MB limit.');
      return;
    }
    setSelectedFile(file);
  }
};

const handleRemoveFile = () => {
  setSelectedFile(null);
  if (fileInputRef.current) {
    fileInputRef.current.value = '';
  }
};
```

Update form JSX:
```jsx
<form className="input-form" onSubmit={handleSubmit}>
  {selectedFile && (
    <div className="file-preview">
      <span className="file-name">📊 {selectedFile.name}</span>
      <button type="button" className="remove-file" onClick={handleRemoveFile}>×</button>
    </div>
  )}
  <div className="input-row">
    <input
      type="file"
      ref={fileInputRef}
      onChange={handleFileChange}
      accept=".csv"
      style={{ display: 'none' }}
    />
    <button
      type="button"
      className="attach-button"
      onClick={() => fileInputRef.current?.click()}
      title="Attach CSV"
    >
      📎
    </button>
    <textarea ... />
    <button type="submit" className="send-button" disabled={!input.trim() && !selectedFile}>
      Send
    </button>
  </div>
</form>
```

Update handleSubmit:
```jsx
const handleSubmit = async (e) => {
  e.preventDefault();
  if (!input.trim() && !selectedFile) return;

  await onSend(input, selectedFile);
  setInput('');
  setSelectedFile(null);
  if (fileInputRef.current) fileInputRef.current.value = '';
};
```

#### 2. Add Styles
**File**: `frontend/src/components/ChatInterface.css`

```css
.input-row {
  display: flex;
  align-items: flex-end;
  gap: 8px;
}

.attach-button {
  padding: 8px 12px;
  background: #f0f0f0;
  border: 1px solid #ddd;
  border-radius: 4px;
  cursor: pointer;
  font-size: 18px;
}

.attach-button:hover {
  background: #e0e0e0;
}

.file-preview {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: #e8f4fc;
  border: 1px solid #b3d7f0;
  border-radius: 4px;
  margin-bottom: 8px;
}

.file-name {
  flex: 1;
  font-size: 14px;
}

.remove-file {
  background: none;
  border: none;
  font-size: 18px;
  cursor: pointer;
}
```

#### 3. Update App.jsx
**File**: `frontend/src/App.jsx`

```jsx
import { sendMessageWithCSVStream } from './api';

const handleSendMessage = async (content, file = null) => {
  // ... existing conversation creation logic ...

  await sendMessageWithCSVStream(
    currentConversation.id,
    content,
    file,
    handleStreamEvent
  );
};
```

### Success Criteria:

#### Automated Verification:
- [x] Frontend builds: `cd frontend && npm run build`

#### Manual Verification:
- [ ] File attach button appears
- [ ] Only .csv files selectable
- [ ] File preview shows with remove option
- [ ] CSV data appears in council responses

---

## Phase 4: Display File Info in Messages

### Overview
Show CSV file indicator in sent messages.

### Changes Required:

**File**: `frontend/src/components/ChatInterface.jsx`

Update message rendering:
```jsx
{msg.role === 'user' && (
  <div className="user-message">
    {msg.file && (
      <div className="message-file-indicator">📊 {msg.file.filename}</div>
    )}
    <div className="markdown-content">{msg.content}</div>
  </div>
)}
```

**File**: `frontend/src/components/ChatInterface.css`

```css
.message-file-indicator {
  display: inline-flex;
  padding: 4px 8px;
  background: #f0f0f0;
  border-radius: 4px;
  font-size: 12px;
  margin-bottom: 8px;
}
```

### Success Criteria:

#### Manual Verification:
- [ ] File indicator shows in sent messages

---

## Testing Strategy

### Manual Testing Steps:
1. Upload a CSV and ask "analyze this data"
2. Upload a CSV with 200+ rows (verify truncation message)
3. Try uploading non-CSV file (verify rejection)
4. Try uploading CSV > 5MB (verify rejection)
5. Send message without file (verify still works)
6. Verify CSV appears as markdown table in council responses

## References

- Original research: `thoughts/shared/research/2025-12-27-file-upload-integration.md`
- FastAPI file upload: https://fastapi.tiangolo.com/tutorial/request-files/
- Pandas to_markdown: https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.to_markdown.html
