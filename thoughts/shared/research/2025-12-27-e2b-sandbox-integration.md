---
date: 2025-12-27T17:32:26+0530
researcher: Claude Code
git_commit: 92e1fccb1bdcf1bab7221aa9ed90f9dc72529131
branch: master
repository: llm-council
topic: "E2B Sandbox Integration for LLM Council Parallel Agent Execution"
tags: [research, codebase, e2b, sandbox, code-execution, parallel-agents]
status: complete
last_updated: 2025-12-27
last_updated_by: Claude Code
---

# Research: E2B Sandbox Integration for LLM Council Parallel Agent Execution

**Date**: 2025-12-27T17:32:26+0530
**Researcher**: Claude Code
**Git Commit**: 92e1fccb1bdcf1bab7221aa9ed90f9dc72529131
**Branch**: master
**Repository**: llm-council

## Research Question

How to integrate E2B sandbox environments with the LLM Council so that when a user asks a query, the council runs parallel agents in sandbox environments with each council model and executes workflows to produce output through messages.

## Summary

The LLM Council currently uses a local Jupyter kernel-based code execution system (`code_executor.py`) that runs synchronously in a thread pool. E2B provides cloud-based isolated sandbox VMs that can replace this local execution, offering better security, scalability, and true parallel execution. The integration would require:

1. Replacing `code_executor.py` with an E2B-based executor
2. Creating parallel sandboxes for each council model
3. Uploading CSV data to each sandbox
4. Executing LLM-generated code in isolated environments
5. Retrieving outputs (stdout, images/charts) from sandboxes

## Detailed Findings

### Current Architecture: Local Jupyter Kernel Execution

The current code execution flow in the LLM Council:

**File: `backend/code_executor.py`**
- Uses `jupyter_client.KernelManager` to start local Python kernels
- Runs synchronously in a thread pool via `concurrent.futures.ThreadPoolExecutor`
- Validates code for blocked imports (os, sys, subprocess, etc.)
- Captures stdout, stderr, and display_data (images)
- Saves generated plots to `data/outputs/` directory
- Default timeout: 30 seconds

**Key Functions:**
- `validate_code(code)` - Security validation (lines 39-61)
- `_run_code_sync(code, csv_path, timeout)` - Synchronous kernel execution (lines 64-171)
- `execute_code_for_model(code, csv_path)` - Async wrapper (lines 174-199)

**File: `backend/council.py`**
- `stage1_collect_responses_with_code()` (lines 625-724) orchestrates parallel code generation
- Uses `asyncio.gather()` to process all models in parallel
- Includes retry logic with error feedback (MAX_CODE_RETRIES = 2)
- Model-specific code hints for problematic models (MODEL_CODE_HINTS dict)

**Current Flow:**
```
User Query + CSV → Models generate code → Local Jupyter kernel executes → Results collected
```

### E2B Sandbox Capabilities

**Installation:**
```bash
pip install e2b-code-interpreter python-dotenv
```

**Key Features:**
1. **Isolated VMs** - Each sandbox is a small isolated VM starting in ~150ms
2. **Multiple Languages** - Python, JavaScript, R, Java, Bash support
3. **Default Timeout** - 5 minutes (configurable via `timeout` parameter)
4. **Parallel Execution** - Can run many sandboxes simultaneously
5. **Storage** - 10GB (Hobby) / 20GB (Pro) disk space per sandbox

**Basic Usage Pattern (Python SDK):**
```python
from e2b_code_interpreter import Sandbox

# Create sandbox
sbx = Sandbox.create()

# Execute code
execution = sbx.run_code('print("Hello, world!")')

# Access results
print(execution.logs)  # stdout/stderr

# Cleanup
sbx.kill()
```

**Streaming Support:**
- `on_stdout`/`on_stderr` callbacks for real-time output
- `on_results` callback for charts, tables, and other outputs
- Enables progressive delivery during execution

**File Operations:**
- `sbx.files.list('/')` - List directory contents
- Upload/download files programmatically
- Full filesystem access within sandbox

### Integration Points

**1. Replace `code_executor.py` with E2B Executor**

Current code at `backend/code_executor.py:174-199`:
```python
async def execute_code_for_model(code: str, csv_path: str) -> Dict[str, Any]:
    # Validate code first
    is_valid, error_msg = validate_code(code)
    if not is_valid:
        return {'stdout': '', 'images': [], 'errors': [error_msg], 'success': False}

    # Run synchronous code in thread pool
    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor() as pool:
        result = await loop.run_in_executor(pool, _run_code_sync, code, csv_path)
    return result
```

Would become E2B-based:
```python
async def execute_code_for_model_e2b(code: str, csv_content: bytes) -> Dict[str, Any]:
    from e2b_code_interpreter import Sandbox

    sbx = Sandbox.create(timeout=60)  # 60-second sandbox
    try:
        # Upload CSV to sandbox
        sbx.files.write('/data/input.csv', csv_content)

        # Initialize with imports + load CSV
        init_code = """
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
df = pd.read_csv('/data/input.csv')
"""
        sbx.run_code(init_code)

        # Execute model's code
        execution = sbx.run_code(code)

        # Collect results
        return {
            'stdout': '\n'.join([log.text for log in execution.logs]),
            'images': [r.data for r in execution.results if r.type == 'image'],
            'errors': [log.text for log in execution.logs if log.is_error],
            'success': not execution.error
        }
    finally:
        sbx.kill()
```

**2. Parallel Sandbox Execution in `council.py`**

Current parallel model processing at `backend/council.py:717-722`:
```python
# Process all models in parallel with retry logic
tasks = [process_model_with_retry(model) for model in COUNCIL_MODELS]
results = await asyncio.gather(*tasks)
```

With E2B, each model would get its own isolated sandbox:
```python
async def process_model_with_sandbox(model: str, csv_content: bytes) -> Optional[Dict]:
    # Generate code via LLM
    response = await query_model(model, messages)
    code = extract_code_from_response(response.get('content', ''))

    # Execute in dedicated E2B sandbox
    exec_result = await execute_code_for_model_e2b(code, csv_content)

    # Retry logic if execution failed
    retry_count = 0
    while not exec_result['success'] and retry_count < MAX_CODE_RETRIES:
        retry_count += 1
        # ... error feedback and retry ...

    return {...}

# Launch all in parallel - each gets own sandbox
tasks = [process_model_with_sandbox(model, csv_content) for model in COUNCIL_MODELS]
results = await asyncio.gather(*tasks)
```

**3. Configuration Updates**

`backend/config.py` would need:
```python
# E2B configuration
E2B_API_KEY = os.getenv("E2B_API_KEY")
E2B_SANDBOX_TIMEOUT = 60  # seconds per sandbox
```

`.env.example` addition:
```
E2B_API_KEY=e2b_***
```

**4. Main.py Endpoint Changes**

The streaming endpoint at `backend/main.py:220-308` would work largely unchanged, as it already streams stage progress events. The underlying execution would just use E2B instead of local Jupyter.

### Current Council Models

From `backend/config.py:16-21`:
```python
COUNCIL_MODELS = [
    "google/gemini-3-flash-preview",
    "deepseek/deepseek-v3.2-speciale",
    "z-ai/glm-4.7",
    "minimax/minimax-m2.1",
]
```

Each of these 4 models would get its own E2B sandbox for code execution, running in parallel.

### API Flow Comparison

**Current Flow (Local Jupyter):**
```
1. User uploads CSV + query
2. CSV saved to disk (csv_processor.py)
3. Each model generates code (parallel via asyncio.gather)
4. Each code executed in shared Jupyter kernel (thread pool)
5. Results collected, images saved to data/outputs/
6. Stage 2 & 3 proceed with results
```

**E2B Flow (Cloud Sandboxes):**
```
1. User uploads CSV + query
2. CSV content kept in memory
3. Each model generates code (parallel via asyncio.gather)
4. For each model: Create sandbox → Upload CSV → Execute code → Collect results → Kill sandbox
5. All 4 sandboxes run in parallel
6. Results collected, images base64-encoded or saved
7. Stage 2 & 3 proceed with results
```

### Advantages of E2B Integration

1. **True Isolation** - Each model's code runs in completely isolated VM
2. **Security** - No risk of malicious code affecting host system
3. **Scalability** - Cloud-based, can scale to many concurrent sandboxes
4. **Reliability** - No Jupyter kernel conflicts or state pollution
5. **Timeout Control** - Per-sandbox configurable timeouts
6. **Streaming** - Built-in support for real-time output streaming

### Challenges and Considerations

1. **API Costs** - E2B is a paid service after free credits
2. **Latency** - Network overhead for cloud execution (~150ms startup)
3. **API Key Management** - Need to store E2B_API_KEY securely
4. **Image Handling** - Need to transfer images from sandbox to storage
5. **File Size Limits** - Need to handle large CSV uploads efficiently

## Code References

- `backend/code_executor.py:1-200` - Current Jupyter-based execution
- `backend/council.py:625-724` - Stage 1 code execution orchestration
- `backend/council.py:12-32` - Model-specific code hints
- `backend/council.py:34-58` - Retry prompt template
- `backend/config.py:16-21` - Council model list
- `backend/main.py:220-308` - Streaming endpoint with CSV support
- `backend/csv_processor.py:67-114` - CSV storage for code execution

## Architecture Documentation

**Current Pattern: Local Kernel Pool**
- Single Jupyter kernel manager
- Thread pool for parallel execution
- Shared filesystem for CSV and outputs
- Code validation via regex patterns

**Proposed Pattern: E2B Cloud Sandboxes**
- Dedicated sandbox per model per request
- True parallel cloud execution
- Isolated filesystems per sandbox
- Built-in security (no local code execution)

## Related Research

- [E2B Documentation](https://e2b.dev/docs)
- [E2B Code Interpreter SDK](https://e2b.dev/docs/code-interpreting)

## Open Questions

1. **Cost Analysis** - What are the E2B costs for typical council usage patterns?
2. **Hybrid Approach** - Should we support both local and E2B execution modes?
3. **Caching** - Can we reuse sandboxes across multiple code executions?
4. **Error Recovery** - How to handle E2B API outages gracefully?
5. **Image Storage** - Should images be stored in E2B storage buckets or transferred locally?
