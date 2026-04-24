---
date: 2025-12-27T12:30:00+0530
researcher: Claude Code
git_commit: 92e1fccb1bdcf1bab7221aa9ed90f9dc72529131
branch: master
repository: llm-council
topic: "Jupyter Notebook Integration for LLM Council Code Execution"
tags: [research, codebase, jupyter, code-execution, council, csv-processing]
status: complete
last_updated: 2025-12-27
last_updated_by: Claude Code
---

# Research: Jupyter Notebook Integration for LLM Council Code Execution

**Date**: 2025-12-27T12:30:00+0530
**Researcher**: Claude Code
**Git Commit**: 92e1fccb1bdcf1bab7221aa9ed90f9dc72529131
**Branch**: master
**Repository**: llm-council

## Research Question

How to add Jupyter Notebook execution capability to the LLM Council so the council can write Python code and execute it against CSV data to analyze all 5000 rows instead of being limited to 100 rows in context?

## Summary

The LLM Council currently processes CSV files by truncating them to 100 rows and embedding the markdown table directly in the user query. This approach limits the council's ability to analyze large datasets.

The codebase has a clear 3-stage architecture:
1. **Stage 1**: Parallel model queries
2. **Stage 2**: Peer ranking with anonymized responses
3. **Stage 3**: Chairman synthesis

To enable code execution, a new code execution layer needs to be added that:
1. Stores the full CSV file on disk (not truncated)
2. Provides a Jupyter kernel for Python code execution
3. Allows council models to write code that gets executed
4. Captures execution results (stdout, dataframes, plots) back to the council

## Detailed Findings

### 1. Current Council Architecture

**File**: `backend/council.py`

The council operates in 3 stages:

#### Stage 1: Collect Responses (Lines 8-32)
```python
async def stage1_collect_responses(user_query: str) -> List[Dict[str, Any]]:
    messages = [{"role": "user", "content": user_query}]
    responses = await query_models_parallel(COUNCIL_MODELS, messages)
```
- Sends user query to all council models in parallel
- Returns list of model responses
- Uses `query_models_parallel()` from `openrouter.py`

#### Stage 2: Collect Rankings (Lines 35-112)
```python
async def stage2_collect_rankings(user_query: str, stage1_results: List[Dict[str, Any]]) -> Tuple[...]:
```
- Creates anonymized labels (Response A, B, C...)
- Each model ranks all responses
- Parses rankings using `parse_ranking_from_text()`

#### Stage 3: Synthesize Final (Lines 115-174)
```python
async def stage3_synthesize_final(user_query: str, stage1_results: List[Dict[str, Any]], stage2_results: List[Dict[str, Any]]) -> Dict[str, Any]:
```
- Chairman model (`CHAIRMAN_MODEL`) synthesizes final answer
- Uses all Stage 1 responses and Stage 2 rankings as context

### 2. Current CSV Processing

**File**: `backend/csv_processor.py`

```python
class CSVProcessor:
    MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
    MAX_ROWS = 100  # Limit rows to prevent context overflow
```

Current behavior:
- Reads CSV using pandas
- **Truncates to 100 rows** (line 46-48)
- Converts to markdown table
- Embeds markdown table directly in user query

**File**: `backend/main.py` (lines 220-223)
```python
user_query = content
if csv_data:
    user_query = f"{content}\n\n[Attached CSV: {csv_data['filename']}]\n{csv_data['content']}"
```

### 3. Streaming Response Architecture

**File**: `backend/main.py` (lines 127-195)

Uses Server-Sent Events (SSE) via FastAPI's `StreamingResponse`:

```python
async def event_generator():
    yield f"data: {json.dumps({'type': 'stage1_start'})}\n\n"
    stage1_results = await stage1_collect_responses(user_query)
    yield f"data: {json.dumps({'type': 'stage1_complete', 'data': stage1_results})}\n\n"
    # ... stages 2 and 3
```

Event types:
- `stage1_start`, `stage1_complete`
- `stage2_start`, `stage2_complete`
- `stage3_start`, `stage3_complete`
- `title_complete`
- `complete`
- `error`

### 4. OpenRouter API Client

**File**: `backend/openrouter.py`

```python
async def query_model(model: str, messages: List[Dict[str, str]], timeout: float = 120.0):
    # Sends request to OpenRouter API
    payload = {"model": model, "messages": messages}
```

Models receive messages in standard format: `[{"role": "user", "content": "..."}]`

### 5. Model Configuration

**File**: `backend/config.py`

```python
COUNCIL_MODELS = [
    "openai/gpt-5-nano",
    "deepseek/deepseek-v3.2-speciale",
    "z-ai/glm-4.7",
    "minimax/minimax-m2.1",
]
CHAIRMAN_MODEL = "moonshotai/kimi-k2-thinking"
```

## Code References

- `backend/council.py:8-32` - Stage 1 collect responses
- `backend/council.py:35-112` - Stage 2 collect rankings
- `backend/council.py:115-174` - Stage 3 synthesize final
- `backend/council.py:296-335` - Full council orchestration
- `backend/csv_processor.py:11-12` - MAX_FILE_SIZE and MAX_ROWS limits
- `backend/csv_processor.py:46-48` - Row truncation logic
- `backend/main.py:198-280` - CSV upload streaming endpoint
- `backend/main.py:220-223` - CSV embedding in query
- `backend/openrouter.py:8-53` - Single model query
- `backend/openrouter.py:56-79` - Parallel model queries

## Architecture Documentation

### Current Data Flow

```
User Query + CSV File
       ↓
CSVProcessor.process_csv() → Truncate to 100 rows → Markdown table
       ↓
Embed markdown in user_query string
       ↓
Stage 1: query_models_parallel(COUNCIL_MODELS, user_query)
       ↓
Stage 2: Anonymize + query_models_parallel(COUNCIL_MODELS, ranking_prompt)
       ↓
Stage 3: query_model(CHAIRMAN_MODEL, synthesis_prompt)
       ↓
StreamingResponse → SSE events → Frontend
```

### Integration Points for Jupyter Execution

1. **File Storage**: Store full CSV in `data/uploads/` directory
2. **Code Execution Service**: New module `backend/code_executor.py`
3. **Modified Council Flow**: Add code execution stage between or within existing stages
4. **New SSE Events**: `code_execution_start`, `code_output`, `code_execution_complete`

### Proposed New Data Flow

```
User Query + CSV File
       ↓
Store full CSV to disk (no truncation)
       ↓
Stage 1: Models write Python code for analysis
       ↓
Execute code via Jupyter kernel → Capture results
       ↓
Stage 2: Rank responses (including code outputs)
       ↓
Stage 3: Chairman synthesis with full analysis
```

## Open Questions

1. Should code execution happen per-model (each model runs its own code) or should there be a shared execution environment?
2. How to handle code execution errors and timeouts?
3. Should plots/visualizations be rendered and returned as images?
4. How to sandbox code execution for security?
5. Should the council iterate (write code → see results → write more code)?
