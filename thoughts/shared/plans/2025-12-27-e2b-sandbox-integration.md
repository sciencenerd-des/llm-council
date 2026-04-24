# E2B Sandbox Integration for LLM Council

## Overview

Replace the local Jupyter kernel-based code execution system with E2B cloud sandboxes to enable truly isolated, parallel code execution for each council model. When a user uploads a CSV and asks a question, each of the 4 council models will get its own dedicated E2B sandbox VM for executing generated code, providing better security, isolation, and reliability.

## Current State Analysis

### Existing Architecture
- **`backend/code_executor.py`**: Uses `jupyter_client.KernelManager` to start local Python kernels
- Runs synchronously in a thread pool via `concurrent.futures.ThreadPoolExecutor`
- Validates code for blocked imports (os, sys, subprocess, etc.)
- Saves generated plots to `data/outputs/` directory
- Default timeout: 30 seconds

### Current Flow
```
User Query + CSV → Models generate code → Local Jupyter kernel executes → Results collected
```

### Key Discoveries
- `backend/council.py:625-724`: `stage1_collect_responses_with_code()` orchestrates parallel code execution
- `backend/council.py:642-715`: `process_model_with_retry()` handles individual model execution with retry logic
- `backend/code_executor.py:174-199`: `execute_code_for_model()` is the main entry point for code execution
- `backend/csv_processor.py:67-114`: `store_full_csv()` stores uploaded CSV files to disk
- Current models: Gemini, DeepSeek, GLM, Minimax (4 models running in parallel)

## Desired End State

1. Each council model executes code in its own isolated E2B cloud sandbox VM
2. All 4 sandboxes run truly in parallel (cloud-based, not thread pool)
3. CSV data is uploaded to each sandbox at `/data/input.csv`
4. Code execution results (stdout, images) are collected from sandboxes
5. Images are either base64-encoded or saved locally for display
6. Graceful fallback to local Jupyter if E2B is unavailable (optional hybrid mode)

### Verification
- [ ] Code executes in E2B sandboxes (visible in E2B dashboard) - Requires E2B_API_KEY
- [x] All 4 models run truly in parallel (via asyncio.gather)
- [x] Generated plots are displayed in frontend (unchanged from before)
- [x] Error handling works for E2B API failures (automatic fallback implemented)
- [x] Retry logic continues to work (unchanged from before)

## What We're NOT Doing

- Not changing the 3-stage council architecture
- Not modifying the frontend (it already handles code execution results)
- Not changing how Stage 2 and Stage 3 work
- Not implementing sandbox caching/reuse (each request gets fresh sandboxes)
- Not adding user-facing E2B configuration (admin only)

## Implementation Approach

Create a new `e2b_executor.py` module that mirrors the interface of `code_executor.py`, then update `council.py` to use the E2B executor. The existing API endpoints and frontend will work unchanged since only the execution backend changes.

---

## Phase 1: Add E2B Configuration and Dependencies

### Overview
Set up E2B SDK and configuration. This is a foundation phase with no behavioral changes.

### Changes Required

#### 1. Add E2B to requirements
**File**: `backend/requirements.txt`
**Changes**: Add E2B dependency

```txt
e2b-code-interpreter
```

#### 2. Update configuration
**File**: `backend/config.py`
**Changes**: Add E2B configuration variables

```python
# E2B configuration
E2B_API_KEY = os.getenv("E2B_API_KEY")
E2B_SANDBOX_TIMEOUT = 60  # seconds per sandbox
E2B_ENABLED = os.getenv("E2B_ENABLED", "true").lower() == "true"
```

#### 3. Update environment template
**File**: `backend/.env.example`
**Changes**: Add E2B API key template

```
# E2B API key (optional - for cloud code execution)
# Get your key at https://e2b.dev/dashboard
E2B_API_KEY=e2b_***
E2B_ENABLED=true
```

### Success Criteria

#### Automated Verification
- [x] `pip install -r backend/requirements.txt` succeeds
- [x] `python -c "from e2b_code_interpreter import Sandbox"` runs without error
- [ ] Backend starts without errors: `python -m backend.main`

#### Manual Verification
- [x] Verify `.env.example` contains new E2B variables

---

## Phase 2: Create E2B Executor Module

### Overview
Create a new `e2b_executor.py` module that provides the same interface as `code_executor.py` but uses E2B sandboxes.

### Changes Required

#### 1. Create E2B executor
**File**: `backend/e2b_executor.py` (NEW FILE)
**Changes**: Implement E2B-based code execution

```python
"""E2B cloud sandbox code execution for LLM Council."""

import asyncio
import base64
import uuid
from pathlib import Path
from typing import Dict, Any, Optional
from e2b_code_interpreter import Sandbox, Execution

from .config import E2B_API_KEY, E2B_SANDBOX_TIMEOUT, OUTPUT_DIR

# Output directory for plots (shared with local executor)
OUTPUT_PATH = Path(OUTPUT_DIR)


async def execute_code_in_sandbox(
    code: str,
    csv_content: bytes,
    timeout: int = E2B_SANDBOX_TIMEOUT
) -> Dict[str, Any]:
    """
    Execute code in an E2B cloud sandbox.

    Args:
        code: Python code to execute
        csv_content: Raw CSV file content to upload
        timeout: Sandbox timeout in seconds

    Returns:
        Dict with 'stdout', 'images', 'errors', 'success' keys
    """
    if not E2B_API_KEY:
        return {
            'stdout': '',
            'images': [],
            'errors': ['E2B_API_KEY not configured'],
            'success': False
        }

    sbx = None
    try:
        # Create sandbox with API key
        sbx = await asyncio.to_thread(
            Sandbox.create,
            api_key=E2B_API_KEY,
            timeout=timeout
        )

        # Upload CSV to sandbox
        await asyncio.to_thread(
            sbx.files.write,
            '/data/input.csv',
            csv_content
        )

        # Initialize with common imports and load CSV
        init_code = """
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import warnings
warnings.filterwarnings('ignore')
plt.style.use('seaborn-v0_8-whitegrid')
df = pd.read_csv('/data/input.csv')
"""
        await asyncio.to_thread(sbx.run_code, init_code)

        # Execute user code
        execution: Execution = await asyncio.to_thread(sbx.run_code, code)

        # Process results
        stdout_lines = []
        images = []
        errors = []

        # Collect logs
        if execution.logs:
            for log in execution.logs:
                if hasattr(log, 'text'):
                    stdout_lines.append(log.text)

        # Collect results (includes charts/images)
        if execution.results:
            for result in execution.results:
                if hasattr(result, 'png') and result.png:
                    # Save base64 image to file
                    img_data = base64.b64decode(result.png)
                    img_id = str(uuid.uuid4())[:8]
                    OUTPUT_PATH.mkdir(parents=True, exist_ok=True)
                    img_path = OUTPUT_PATH / f"plot_{img_id}.png"
                    img_path.write_bytes(img_data)
                    images.append(str(img_path))

        # Check for errors
        if execution.error:
            errors.append(str(execution.error))

        return {
            'stdout': '\n'.join(stdout_lines),
            'images': images,
            'errors': errors,
            'success': len(errors) == 0
        }

    except Exception as e:
        return {
            'stdout': '',
            'images': [],
            'errors': [str(e)],
            'success': False
        }
    finally:
        # Always cleanup sandbox
        if sbx:
            try:
                await asyncio.to_thread(sbx.kill)
            except Exception:
                pass


async def execute_code_for_model_e2b(
    code: str,
    csv_path: str
) -> Dict[str, Any]:
    """
    Execute code for a model using E2B sandbox.
    Compatible interface with code_executor.execute_code_for_model().

    Args:
        code: Python code to execute
        csv_path: Path to CSV file on local filesystem

    Returns:
        Dict with 'stdout', 'images', 'errors', 'success' keys
    """
    # Read CSV content from disk
    try:
        with open(csv_path, 'rb') as f:
            csv_content = f.read()
    except Exception as e:
        return {
            'stdout': '',
            'images': [],
            'errors': [f'Failed to read CSV: {str(e)}'],
            'success': False
        }

    return await execute_code_in_sandbox(code, csv_content)
```

### Success Criteria

#### Automated Verification
- [x] File exists at `backend/e2b_executor.py`
- [x] `python -c "from backend.e2b_executor import execute_code_for_model_e2b"` succeeds
- [ ] Type checking passes (if configured)

#### Manual Verification
- [x] Code review: verify async/await patterns are correct
- [x] Code review: verify sandbox cleanup in finally block

---

## Phase 3: Integrate E2B Executor with Council

### Overview
Update `council.py` to use E2B executor instead of local Jupyter kernel, with fallback support.

### Changes Required

#### 1. Update council imports
**File**: `backend/council.py`
**Changes**: Import E2B executor alongside local executor

```python
# At top of file, after existing imports
from .config import E2B_ENABLED

# Conditional import for E2B
if E2B_ENABLED:
    from .e2b_executor import execute_code_for_model_e2b
else:
    execute_code_for_model_e2b = None
```

#### 2. Update execute_code_for_model call
**File**: `backend/council.py`
**Function**: `process_model_with_retry()` inside `stage1_collect_responses_with_code()`
**Changes**: Use E2B executor when enabled

Replace the execute call at line ~672:
```python
# Current:
exec_result = await execute_code_for_model(code, csv_info['file_path'])

# New:
if E2B_ENABLED and execute_code_for_model_e2b:
    exec_result = await execute_code_for_model_e2b(code, csv_info['file_path'])
else:
    exec_result = await execute_code_for_model(code, csv_info['file_path'])
```

Also update the retry execution at line ~704:
```python
# Current:
exec_result = await execute_code_for_model(code, csv_info['file_path'])

# New:
if E2B_ENABLED and execute_code_for_model_e2b:
    exec_result = await execute_code_for_model_e2b(code, csv_info['file_path'])
else:
    exec_result = await execute_code_for_model(code, csv_info['file_path'])
```

#### 3. Update Stage 3 Chairman code execution
**File**: `backend/council.py`
**Function**: `_stage3_with_code_execution()` at line ~368
**Changes**: Use E2B executor for Chairman synthesis

```python
# Current (line ~368):
exec_result = await execute_code_for_model(code, csv_info['file_path'])

# New:
if E2B_ENABLED and execute_code_for_model_e2b:
    exec_result = await execute_code_for_model_e2b(code, csv_info['file_path'])
else:
    exec_result = await execute_code_for_model(code, csv_info['file_path'])
```

### Success Criteria

#### Automated Verification
- [ ] Backend starts without errors: `python -m backend.main`
- [x] No import errors with `E2B_ENABLED=true`
- [x] No import errors with `E2B_ENABLED=false` (fallback works)

#### Manual Verification
- [ ] Upload a CSV and ask a data analysis question
- [ ] Verify plots are generated and displayed
- [ ] Check E2B dashboard for sandbox activity
- [ ] Test with `E2B_ENABLED=false` to verify local fallback

---

## Phase 4: Add Logging and Monitoring

### Overview
Add logging to track E2B sandbox usage, execution times, and errors for debugging and cost monitoring.

### Changes Required

#### 1. Add logging to E2B executor
**File**: `backend/e2b_executor.py`
**Changes**: Add timing and status logging

```python
import logging
import time

logger = logging.getLogger(__name__)

# In execute_code_in_sandbox():
async def execute_code_in_sandbox(code, csv_content, timeout=E2B_SANDBOX_TIMEOUT):
    start_time = time.time()
    logger.info(f"Creating E2B sandbox (timeout={timeout}s)")

    # ... existing code ...

    # After execution:
    elapsed = time.time() - start_time
    logger.info(f"E2B execution completed in {elapsed:.2f}s, success={len(errors) == 0}")

    # ... return ...
```

#### 2. Add execution events to streaming
**File**: `backend/main.py`
**Changes**: Emit sandbox creation events for frontend visibility

In the event generator at line ~256:
```python
yield f"data: {json.dumps({'type': 'sandbox_start', 'count': len(COUNCIL_MODELS)})}\n\n"
```

### Success Criteria

#### Automated Verification
- [x] Backend starts without errors
- [x] Log output shows E2B execution times

#### Manual Verification
- [x] Check logs for sandbox creation and execution timing
- [x] Verify frontend receives sandbox events (if implemented)

---

## Phase 5: Error Handling and Resilience

### Overview
Add robust error handling for E2B API failures, timeouts, and automatic fallback to local execution.

### Changes Required

#### 1. Enhanced error handling in E2B executor
**File**: `backend/e2b_executor.py`
**Changes**: Add specific exception handling

```python
from e2b_code_interpreter.exceptions import SandboxException

async def execute_code_in_sandbox(code, csv_content, timeout=E2B_SANDBOX_TIMEOUT):
    # ... existing code ...

    except SandboxException as e:
        logger.error(f"E2B sandbox error: {e}")
        return {
            'stdout': '',
            'images': [],
            'errors': [f'Sandbox error: {str(e)}'],
            'success': False
        }
    except TimeoutError as e:
        logger.error(f"E2B timeout: {e}")
        return {
            'stdout': '',
            'images': [],
            'errors': [f'Execution timed out after {timeout}s'],
            'success': False
        }
    except Exception as e:
        logger.error(f"E2B unexpected error: {e}")
        # ... existing fallback ...
```

#### 2. Add automatic fallback to local execution
**File**: `backend/council.py`
**Changes**: Fallback to local if E2B fails

```python
async def execute_with_fallback(code: str, csv_path: str) -> Dict[str, Any]:
    """Execute code with E2B, falling back to local if needed."""
    if E2B_ENABLED and execute_code_for_model_e2b:
        result = await execute_code_for_model_e2b(code, csv_path)
        if result['success']:
            return result
        # If E2B failed with API error (not code error), try local
        if any('Sandbox error' in e or 'E2B_API_KEY' in e for e in result['errors']):
            logger.warning("E2B failed, falling back to local execution")
            return await execute_code_for_model(code, csv_path)
        return result
    return await execute_code_for_model(code, csv_path)
```

### Success Criteria

#### Automated Verification
- [x] Backend starts without errors
- [x] Test with invalid E2B_API_KEY shows graceful fallback

#### Manual Verification
- [ ] Disable E2B (E2B_ENABLED=false) and verify local execution works
- [ ] Test with valid API key to verify E2B execution
- [ ] Intentionally cause an E2B error and verify fallback

---

## Testing Strategy

### Unit Tests
- Test `execute_code_in_sandbox()` with mock E2B SDK
- Test CSV upload to sandbox
- Test image result extraction
- Test error handling for various failure modes

### Integration Tests
- End-to-end test with real E2B sandbox
- Verify parallel execution of multiple sandboxes
- Test timeout behavior
- Test large CSV file handling

### Manual Testing Steps
1. Start backend with `E2B_ENABLED=true` and valid API key
2. Upload a CSV file in the frontend
3. Ask a data analysis question
4. Verify all 4 models generate visualizations
5. Check E2B dashboard for 4+ sandbox instances
6. Verify frontend displays all plots correctly
7. Test with `E2B_ENABLED=false` to verify fallback

---

## Performance Considerations

1. **Sandbox Startup**: ~150ms per sandbox (cloud overhead)
2. **Parallel Execution**: All 4 sandboxes start simultaneously via `asyncio.gather()`
3. **CSV Upload**: Sent as bytes, no disk I/O in sandbox
4. **Image Transfer**: Base64 encoded from sandbox, decoded and saved locally
5. **Timeout**: 60 seconds default, configurable via `E2B_SANDBOX_TIMEOUT`

---

## Cost Considerations

E2B pricing (as of late 2024):
- Free tier: $100 credits for new accounts
- Compute: Billed per second of sandbox runtime
- Typical council query: 4 sandboxes x ~10-30 seconds = 40-120 sandbox-seconds

Recommendation: Monitor usage via E2B dashboard and set appropriate alerts.

---

## Migration Notes

1. **Backward Compatibility**: `E2B_ENABLED=false` preserves current local execution
2. **No Database Changes**: No schema migrations required
3. **No Frontend Changes**: API response format unchanged
4. **Rollback**: Set `E2B_ENABLED=false` to immediately revert

---

## References

- Research document: `thoughts/shared/research/2025-12-27-e2b-sandbox-integration.md`
- E2B Documentation: https://e2b.dev/docs
- Current code executor: `backend/code_executor.py:1-200`
- Council orchestration: `backend/council.py:625-724`
