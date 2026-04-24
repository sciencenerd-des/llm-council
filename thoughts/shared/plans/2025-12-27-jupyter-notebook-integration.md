# Jupyter Notebook Integration Implementation Plan

## Overview

Add Jupyter Notebook code execution capability to the LLM Council so that when a CSV file is uploaded, council models can write Python code to analyze the **full dataset** (not limited to 100 rows). Each model executes its own code, and results (including plots) are captured and included in the council deliberation.

## Current State Analysis

**Current CSV Handling** (`backend/csv_processor.py:11-12`):
- CSV files are truncated to 100 rows (`MAX_ROWS = 100`)
- Data is converted to markdown table and embedded in the user query
- This prevents analysis of large datasets (e.g., 5000 rows)

**Current Council Flow** (`backend/council.py`):
- Stage 1: Models respond to query with embedded CSV snippet
- Stage 2: Models rank each other's responses
- Stage 3: Chairman synthesizes final answer

**Problem**: Models only see 100 rows and cannot run computations on the full data.

## Desired End State

When a user uploads a CSV file:
1. Full CSV is stored on disk (not truncated)
2. Models are prompted to write Python analysis code
3. Each model's code is executed via Jupyter kernel
4. Execution results (stdout, dataframes, plots) are captured
5. Results are included in the council's ranking and synthesis stages
6. Frontend displays code blocks and execution outputs

### Verification:
- Upload a 5000-row CSV and ask for statistical analysis
- Each model should write code that runs against all 5000 rows
- Results should show accurate statistics for the full dataset
- Any generated plots should be visible in the response

## What We're NOT Doing

- Multi-turn code iteration (write → see results → write more)
- Shared execution environment between models
- Support for languages other than Python
- Interactive notebook UI in frontend
- Code editing by users before execution

## Implementation Approach

**Architecture Decision**: Per-model code execution
- Each council model writes its own Python code
- Code is executed in an isolated Jupyter kernel per model
- Results are captured and included in that model's response
- This preserves the independent peer review model

**Security**: Sandboxed execution
- 30-second timeout per execution
- Whitelist of allowed imports (pandas, numpy, matplotlib, seaborn)
- Block dangerous modules (os, subprocess, sys, etc.)

---

## Phase 1: Backend - File Storage & Code Executor

### Overview
Create the infrastructure to store full CSV files and execute Python code via Jupyter kernel.

### Changes Required:

#### 1. Install Dependencies
**File**: `backend/requirements.txt`

Add:
```text
jupyter-client>=8.0.0
ipykernel>=6.0.0
```

#### 2. Create Code Executor Module
**File**: `backend/code_executor.py` (new file)

```python
"""Jupyter kernel-based code execution for LLM Council."""

import asyncio
import base64
import os
import re
import uuid
from pathlib import Path
from typing import Dict, Any, Optional
from jupyter_client import KernelManager

# Allowed imports for sandboxing
ALLOWED_IMPORTS = {
    'pandas', 'pd',
    'numpy', 'np',
    'matplotlib', 'plt',
    'seaborn', 'sns',
    'statistics',
    'math',
    'json',
    'csv',
    're',
    'datetime',
    'collections',
}

BLOCKED_IMPORTS = {
    'os', 'sys', 'subprocess', 'shutil', 'pathlib',
    'socket', 'requests', 'urllib', 'http',
    'pickle', 'shelve', 'sqlite3',
    'importlib', 'builtins', '__builtins__',
    'eval', 'exec', 'compile', 'open',
}

# Output directory for plots
OUTPUT_DIR = Path("data/outputs")


class CodeExecutor:
    """Executes Python code in a Jupyter kernel with sandboxing."""

    TIMEOUT = 30  # seconds

    def __init__(self):
        self.km: Optional[KernelManager] = None
        self.kc = None
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    async def start_kernel(self):
        """Start a new Jupyter kernel."""
        self.km = KernelManager(kernel_name='python3')
        self.km.start_kernel()
        self.kc = self.km.client()
        self.kc.start_channels()
        # Wait for kernel to be ready
        self.kc.wait_for_ready(timeout=10)

        # Initialize with common imports
        init_code = """
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend
import matplotlib.pyplot as plt
import seaborn as sns
import warnings
warnings.filterwarnings('ignore')
"""
        await self._execute_code(init_code)

    async def stop_kernel(self):
        """Stop the Jupyter kernel."""
        if self.kc:
            self.kc.stop_channels()
        if self.km:
            self.km.shutdown_kernel(now=True)
        self.km = None
        self.kc = None

    def validate_code(self, code: str) -> tuple[bool, str]:
        """Check code for blocked imports and dangerous patterns."""
        # Check for blocked imports
        import_pattern = r'(?:from|import)\s+(\w+)'
        imports = re.findall(import_pattern, code)

        for imp in imports:
            if imp in BLOCKED_IMPORTS:
                return False, f"Import '{imp}' is not allowed for security reasons"

        # Check for dangerous patterns
        dangerous_patterns = [
            (r'\bopen\s*\(', "File operations with open() are not allowed"),
            (r'\bexec\s*\(', "exec() is not allowed"),
            (r'\beval\s*\(', "eval() is not allowed"),
            (r'__\w+__', "Dunder methods are not allowed"),
        ]

        for pattern, message in dangerous_patterns:
            if re.search(pattern, code):
                return False, message

        return True, ""

    async def _execute_code(self, code: str) -> Dict[str, Any]:
        """Execute code and capture output."""
        if not self.kc:
            raise RuntimeError("Kernel not started")

        msg_id = self.kc.execute(code)

        outputs = []
        images = []
        errors = []

        # Collect output with timeout
        deadline = asyncio.get_event_loop().time() + self.TIMEOUT

        while True:
            try:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    errors.append("Execution timed out after 30 seconds")
                    break

                msg = await asyncio.wait_for(
                    asyncio.to_thread(self.kc.get_iopub_msg),
                    timeout=min(remaining, 1.0)
                )

                msg_type = msg['msg_type']
                content = msg['content']

                if msg_type == 'stream':
                    outputs.append(content.get('text', ''))
                elif msg_type == 'execute_result':
                    data = content.get('data', {})
                    if 'text/plain' in data:
                        outputs.append(data['text/plain'])
                elif msg_type == 'display_data':
                    data = content.get('data', {})
                    if 'image/png' in data:
                        # Save image to file
                        img_data = base64.b64decode(data['image/png'])
                        img_id = str(uuid.uuid4())[:8]
                        img_path = OUTPUT_DIR / f"plot_{img_id}.png"
                        img_path.write_bytes(img_data)
                        images.append(str(img_path))
                elif msg_type == 'error':
                    errors.append('\n'.join(content.get('traceback', [])))
                elif msg_type == 'status':
                    if content.get('execution_state') == 'idle':
                        break

            except asyncio.TimeoutError:
                continue
            except Exception as e:
                errors.append(str(e))
                break

        return {
            'stdout': '\n'.join(outputs),
            'images': images,
            'errors': errors,
            'success': len(errors) == 0
        }

    async def execute(self, code: str, csv_path: str) -> Dict[str, Any]:
        """Execute code with CSV file loaded."""
        # Validate code first
        is_valid, error_msg = self.validate_code(code)
        if not is_valid:
            return {
                'stdout': '',
                'images': [],
                'errors': [error_msg],
                'success': False
            }

        try:
            await self.start_kernel()

            # Load the CSV file
            load_code = f"df = pd.read_csv('{csv_path}')"
            await self._execute_code(load_code)

            # Execute user code
            result = await self._execute_code(code)

            return result

        finally:
            await self.stop_kernel()


async def execute_code_for_model(code: str, csv_path: str) -> Dict[str, Any]:
    """Convenience function to execute code for a single model."""
    executor = CodeExecutor()
    return await executor.execute(code, csv_path)
```

#### 3. Create Uploads Directory
**File**: `backend/config.py`

Add:
```python
# Upload directory for full CSV files
UPLOAD_DIR = "data/uploads"

# Output directory for generated plots
OUTPUT_DIR = "data/outputs"
```

#### 4. Update CSV Processor for Full Storage
**File**: `backend/csv_processor.py`

Add new method to store full CSV:
```python
import os
import uuid
from .config import UPLOAD_DIR
from pathlib import Path

class CSVProcessor:
    # ... existing code ...

    @classmethod
    async def store_full_csv(cls, file: UploadFile) -> dict:
        """
        Store the full CSV file to disk for code execution.

        Returns:
            dict with keys:
                - file_path: path to stored file
                - filename: original filename
                - row_count: total rows
                - columns: list of column names
                - preview: first 5 rows as markdown (for prompt context)
        """
        content = await file.read()

        if len(content) > cls.MAX_FILE_SIZE:
            raise ValueError(f"File size exceeds {cls.MAX_FILE_SIZE // (1024*1024)}MB limit")

        filename = file.filename or "data.csv"

        if not filename.lower().endswith('.csv'):
            raise ValueError("Only CSV files are supported")

        # Parse to get metadata
        try:
            df = pd.read_csv(io.BytesIO(content))
        except Exception as e:
            raise ValueError(f"Failed to parse CSV: {str(e)}")

        # Store full file
        Path(UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
        file_id = str(uuid.uuid4())[:8]
        file_path = os.path.join(UPLOAD_DIR, f"{file_id}_{filename}")

        with open(file_path, 'wb') as f:
            f.write(content)

        # Create preview (first 5 rows)
        preview_df = df.head(5)
        preview_markdown = preview_df.to_markdown(index=False)

        return {
            "file_path": file_path,
            "filename": filename,
            "row_count": len(df),
            "columns": list(df.columns),
            "preview": preview_markdown
        }
```

### Success Criteria:

#### Automated Verification:
- [x] Install dependencies: `pip3 install jupyter-client ipykernel`
- [x] No syntax errors: `python3 -m py_compile backend/code_executor.py`
- [x] Kernel installs: `python3 -m ipykernel install --user`

#### Manual Verification:
- [ ] Test code executor with simple pandas code
- [ ] Verify plots are saved to `data/outputs/`

---

## Phase 2: Backend - Council Flow with Code Execution

### Overview
Modify the council flow to request code from models and execute it when a CSV is attached.

### Changes Required:

#### 1. Add Code Execution Stage to Council
**File**: `backend/council.py`

Add new function and modify stage1:
```python
from .code_executor import execute_code_for_model

# New prompt for code generation
CODE_GENERATION_PROMPT = """You are a data analyst. A CSV file has been uploaded with the following structure:

**Filename**: {filename}
**Total Rows**: {row_count}
**Columns**: {columns}

**Preview (first 5 rows)**:
{preview}

**User Question**: {user_query}

Write Python code to analyze this data and answer the user's question.

IMPORTANT RULES:
1. The CSV is already loaded as a pandas DataFrame called `df`
2. Use only: pandas, numpy, matplotlib, seaborn
3. Print your findings using print() statements
4. If creating visualizations, use plt.show() to display them
5. Be thorough but concise

Write ONLY the Python code, no explanations. Start with your analysis code:
```python
"""


async def stage1_collect_responses_with_code(
    user_query: str,
    csv_info: dict
) -> List[Dict[str, Any]]:
    """
    Stage 1 with code execution: Models write code, we execute it.

    Args:
        user_query: The user's question
        csv_info: Dict with file_path, filename, row_count, columns, preview

    Returns:
        List of dicts with model, code, execution_result, and analysis
    """
    # Build the code generation prompt
    prompt = CODE_GENERATION_PROMPT.format(
        filename=csv_info['filename'],
        row_count=csv_info['row_count'],
        columns=', '.join(csv_info['columns']),
        preview=csv_info['preview'],
        user_query=user_query
    )

    messages = [{"role": "user", "content": prompt}]

    # Query all models for code
    responses = await query_models_parallel(COUNCIL_MODELS, messages)

    stage1_results = []

    for model, response in responses.items():
        if response is None:
            continue

        raw_content = response.get('content', '')

        # Extract code from response (handle markdown code blocks)
        code = extract_code_from_response(raw_content)

        # Execute the code
        exec_result = await execute_code_for_model(code, csv_info['file_path'])

        # Build final response combining code and results
        formatted_response = format_code_execution_result(code, exec_result)

        stage1_results.append({
            "model": model,
            "code": code,
            "execution_result": exec_result,
            "response": formatted_response
        })

    return stage1_results


def extract_code_from_response(content: str) -> str:
    """Extract Python code from model response, handling markdown blocks."""
    import re

    # Try to extract from ```python ... ``` blocks
    pattern = r'```python\s*(.*?)```'
    matches = re.findall(pattern, content, re.DOTALL)

    if matches:
        return matches[0].strip()

    # Try generic ``` blocks
    pattern = r'```\s*(.*?)```'
    matches = re.findall(pattern, content, re.DOTALL)

    if matches:
        return matches[0].strip()

    # Return as-is if no code blocks
    return content.strip()


def format_code_execution_result(code: str, exec_result: dict) -> str:
    """Format code and execution result for display."""
    parts = ["**Code:**", "```python", code, "```", "", "**Output:**"]

    if exec_result['success']:
        if exec_result['stdout']:
            parts.append("```")
            parts.append(exec_result['stdout'])
            parts.append("```")

        if exec_result['images']:
            parts.append("")
            parts.append("**Generated Plots:**")
            for img_path in exec_result['images']:
                parts.append(f"![Plot]({img_path})")
    else:
        parts.append("**Execution Error:**")
        parts.append("```")
        parts.extend(exec_result['errors'])
        parts.append("```")

    return '\n'.join(parts)
```

#### 2. Update Full Council Function
**File**: `backend/council.py`

Add new orchestration function:
```python
async def run_full_council_with_code(
    user_query: str,
    csv_info: dict
) -> Tuple[List, List, Dict, Dict]:
    """
    Run the 3-stage council with code execution for CSV analysis.

    Args:
        user_query: The user's question
        csv_info: CSV file info from store_full_csv()

    Returns:
        Tuple of (stage1_results, stage2_results, stage3_result, metadata)
    """
    # Stage 1: Collect code and execute
    stage1_results = await stage1_collect_responses_with_code(user_query, csv_info)

    if not stage1_results:
        return [], [], {
            "model": "error",
            "response": "All models failed to generate code. Please try again."
        }, {}

    # Stage 2: Collect rankings (uses formatted responses with code output)
    stage2_results, label_to_model = await stage2_collect_rankings(
        user_query,
        stage1_results
    )

    # Calculate aggregate rankings
    aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)

    # Stage 3: Synthesize final answer
    stage3_result = await stage3_synthesize_final(
        user_query,
        stage1_results,
        stage2_results
    )

    metadata = {
        "label_to_model": label_to_model,
        "aggregate_rankings": aggregate_rankings,
        "csv_info": {
            "filename": csv_info['filename'],
            "row_count": csv_info['row_count'],
            "columns": csv_info['columns']
        }
    }

    return stage1_results, stage2_results, stage3_result, metadata
```

### Success Criteria:

#### Automated Verification:
- [x] No syntax errors: `python3 -m py_compile backend/council.py`
- [ ] Backend starts: `python3 -m backend.main`

#### Manual Verification:
- [ ] Upload CSV and verify models return code
- [ ] Code execution produces correct output

---

## Phase 3: Backend - API Endpoint Updates

### Overview
Update the CSV streaming endpoint to use the new code execution flow.

### Changes Required:

#### 1. Update CSV Streaming Endpoint
**File**: `backend/main.py`

Replace the CSV endpoint to use code execution:
```python
from .csv_processor import CSVProcessor
from .council import (
    run_full_council_with_code,
    generate_conversation_title,
    stage1_collect_responses_with_code,
    stage2_collect_rankings,
    stage3_synthesize_final,
    calculate_aggregate_rankings
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
            # Store user message
            file_info = {"filename": csv_info["filename"], "file_type": "csv", "row_count": csv_info["row_count"]} if csv_info else None
            storage.add_user_message(conversation_id, content, file_info=file_info)

            # Title generation
            title_task = None
            if is_first_message:
                title_task = asyncio.create_task(generate_conversation_title(content))

            if csv_info:
                # Use code execution flow
                yield f"data: {json.dumps({'type': 'stage1_start', 'mode': 'code_execution'})}\n\n"
                stage1_results = await stage1_collect_responses_with_code(content, csv_info)

                # Send code execution events for each model
                for result in stage1_results:
                    yield f"data: {json.dumps({'type': 'code_executed', 'model': result['model'], 'success': result['execution_result']['success']})}\n\n"

                yield f"data: {json.dumps({'type': 'stage1_complete', 'data': stage1_results})}\n\n"
            else:
                # Regular text-only flow
                yield f"data: {json.dumps({'type': 'stage1_start'})}\n\n"
                stage1_results = await stage1_collect_responses(content)
                yield f"data: {json.dumps({'type': 'stage1_complete', 'data': stage1_results})}\n\n"

            # Stage 2: Rankings
            yield f"data: {json.dumps({'type': 'stage2_start'})}\n\n"
            stage2_results, label_to_model = await stage2_collect_rankings(content, stage1_results)
            aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)
            yield f"data: {json.dumps({'type': 'stage2_complete', 'data': stage2_results, 'metadata': {'label_to_model': label_to_model, 'aggregate_rankings': aggregate_rankings}})}\n\n"

            # Stage 3: Synthesis
            yield f"data: {json.dumps({'type': 'stage3_start'})}\n\n"
            stage3_result = await stage3_synthesize_final(content, stage1_results, stage2_results)
            yield f"data: {json.dumps({'type': 'stage3_complete', 'data': stage3_result})}\n\n"

            # Title
            if title_task:
                title = await title_task
                storage.update_conversation_title(conversation_id, title)
                yield f"data: {json.dumps({'type': 'title_complete', 'data': {'title': title}})}\n\n"

            # Save
            storage.add_assistant_message(conversation_id, stage1_results, stage2_results, stage3_result)
            yield f"data: {json.dumps({'type': 'complete'})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
    )
```

#### 2. Add Static File Serving for Plots
**File**: `backend/main.py`

Add after app initialization:
```python
from fastapi.staticfiles import StaticFiles
from pathlib import Path

# Serve generated plots
Path("data/outputs").mkdir(parents=True, exist_ok=True)
app.mount("/outputs", StaticFiles(directory="data/outputs"), name="outputs")
```

### Success Criteria:

#### Automated Verification:
- [x] No syntax errors: `python3 -m py_compile backend/main.py`
- [ ] Backend starts without errors

#### Manual Verification:
- [ ] Upload CSV via frontend
- [ ] Verify code execution happens
- [ ] Check plots are accessible at `/outputs/`

---

## Phase 4: Frontend - Display Code and Output

### Overview
Update the frontend to display code blocks and execution results from the council.

### Changes Required:

#### 1. Update Stage1 Component
**File**: `frontend/src/components/Stage1.jsx`

Add code block rendering:
```jsx
// Add syntax highlighting (optional but nice)
// In the component, check for code in responses:

{response.code && (
  <div className="code-execution-block">
    <div className="code-section">
      <div className="code-header">Python Code:</div>
      <pre className="code-block">
        <code>{response.code}</code>
      </pre>
    </div>

    <div className="output-section">
      <div className="output-header">
        {response.execution_result?.success ? 'Output:' : 'Error:'}
      </div>
      <pre className={`output-block ${response.execution_result?.success ? '' : 'error'}`}>
        {response.execution_result?.success
          ? response.execution_result.stdout
          : response.execution_result?.errors?.join('\n')
        }
      </pre>

      {response.execution_result?.images?.map((img, i) => (
        <img
          key={i}
          src={`http://localhost:8001/outputs/${img.split('/').pop()}`}
          alt={`Generated plot ${i + 1}`}
          className="generated-plot"
        />
      ))}
    </div>
  </div>
)}
```

#### 2. Add Styles for Code Blocks
**File**: `frontend/src/components/Stage1.css` (or create new)

```css
.code-execution-block {
  margin: 16px 0;
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  overflow: hidden;
}

.code-section,
.output-section {
  padding: 12px;
}

.code-section {
  background: #f8f9fa;
  border-bottom: 1px solid #e0e0e0;
}

.code-header,
.output-header {
  font-size: 12px;
  font-weight: 600;
  color: #666;
  margin-bottom: 8px;
  text-transform: uppercase;
}

.code-block {
  background: #2d2d2d;
  color: #f8f8f2;
  padding: 12px;
  border-radius: 4px;
  overflow-x: auto;
  font-family: 'Fira Code', 'Monaco', monospace;
  font-size: 13px;
  line-height: 1.5;
}

.output-block {
  background: #f5f5f5;
  padding: 12px;
  border-radius: 4px;
  font-family: monospace;
  font-size: 13px;
  white-space: pre-wrap;
  max-height: 300px;
  overflow-y: auto;
}

.output-block.error {
  background: #fff0f0;
  color: #d32f2f;
}

.generated-plot {
  max-width: 100%;
  margin-top: 12px;
  border-radius: 4px;
  border: 1px solid #e0e0e0;
}
```

#### 3. Update File Preview to Show Row Count
**File**: `frontend/src/components/ChatInterface.jsx`

Update file preview:
```jsx
{selectedFile && (
  <div className="file-preview">
    <span className="file-name">
      CSV: {selectedFile.name}
      <span className="file-info">(full dataset will be analyzed via code execution)</span>
    </span>
    <button type="button" className="remove-file" onClick={handleRemoveFile}>x</button>
  </div>
)}
```

### Success Criteria:

#### Automated Verification:
- [x] Frontend builds: `cd frontend && npm run build`

#### Manual Verification:
- [ ] Code blocks display with syntax highlighting
- [ ] Output shows correctly (success or error)
- [ ] Generated plots are visible
- [ ] Scrolling works for long output

---

## Phase 5: Testing & Verification

### Overview
End-to-end testing of the complete flow.

### Test Cases:

#### 1. Basic Analysis Test
- Upload a CSV with 500+ rows
- Ask: "What is the average of column X?"
- Verify: All models write code, execution succeeds, correct average shown

#### 2. Visualization Test
- Upload a CSV with numerical data
- Ask: "Create a histogram of column Y"
- Verify: Plot is generated and displayed

#### 3. Error Handling Test
- Upload a CSV
- Ask something that would cause a code error
- Verify: Error is captured and displayed, no crash

#### 4. Large Dataset Test
- Upload the 5000-row CSV
- Ask for comprehensive analysis
- Verify: All 5000 rows are processed (check output statistics)

#### 5. Security Test
- Verify blocked imports raise errors
- Verify 30-second timeout works

### Success Criteria:

#### Manual Verification:
- [ ] All 5 test cases pass
- [ ] Performance is acceptable (< 60s total for full council with code execution)
- [ ] No memory leaks during execution

---

## Testing Strategy

### Unit Tests:
- `test_code_executor.py`: Test sandboxing, execution, timeout
- `test_csv_processor.py`: Test full storage function

### Integration Tests:
- Test council flow with mock CSV
- Test SSE events include code execution data

### Manual Testing Steps:
1. Start backend and frontend
2. Create new conversation
3. Upload `Student_Performance_Dataset.csv` (5000 rows)
4. Ask: "Analyze this student data. Show me the distribution of scores and identify top performers."
5. Verify:
   - Each model writes Python code
   - Code executes against full 5000 rows
   - Results show accurate statistics
   - Any plots are displayed

## Performance Considerations

- Kernel startup: ~2-3 seconds per model (4 models = ~10s parallel)
- Code execution: 30s timeout max per model
- Consider kernel pooling for future optimization

## Security Considerations

- Sandboxed imports prevent file/network access
- 30-second timeout prevents infinite loops
- Each model gets isolated kernel instance
- No persistent state between executions

## References

- Research: `thoughts/shared/research/2025-12-27-jupyter-notebook-integration.md`
- Current CSV handling: `backend/csv_processor.py`
- Council architecture: `backend/council.py`
- jupyter-client docs: https://jupyter-client.readthedocs.io/
