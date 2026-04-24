# Progressive Streaming & Stage 2 Sandboxing Implementation Plan

## Overview

This plan addresses two issues:
1. **Frontend latency**: Stage 1 results don't appear until ALL models complete (can take 60-180s)
2. **Stage 2 capabilities**: Peer review is text-only, cannot verify claims or propose alternative analyses

We will implement progressive streaming for Stage 1 and Stage 2, plus add sandboxed code execution to Stage 2.

## Current State Analysis

### Stage 1 Latency Problem
- `stage1_collect_responses_with_code()` uses `asyncio.gather(*tasks)` at `council.py:849-850`
- This blocks until ALL models complete before returning ANY results
- `code_executed` events are emitted AFTER all models finish (`main.py:262-263`) - useless for progress
- Frontend has no handler for `code_executed` events - they're ignored

### Stage 2 Current Implementation
- Pure text-based peer review (`council.py:251-328`)
- Models receive formatted output (code + stdout + plot references)
- No ability to verify claims or run alternative analyses
- All infrastructure for code execution exists but isn't used in Stage 2

## Desired End State

### After Implementation:
1. **Progressive Stage 1**: Each model's tab appears immediately when it completes (15-30s for first result instead of 60-180s for all)
2. **Progressive Stage 2**: Same pattern - each peer review appears as it completes
3. **Stage 2 Code Execution**: Models can write verification code and alternative analyses, executed in sandbox

### Verification:
- Start backend, submit CSV query
- First model result should appear within ~30s
- Subsequent results should appear as each model finishes
- Stage 2 should show code execution results alongside rankings
- Total perceived latency should drop from "wait for slowest" to "see results as they arrive"

## What We're NOT Doing

- Not changing the 3-stage architecture
- Not changing model selection or configuration
- Not adding new API endpoints (reusing existing streaming endpoints)
- Not modifying storage schema (execution results stored in existing JSONB fields)
- Not changing Stage 3 (Chairman report) behavior

## Implementation Approach

Replace batch processing with async generators that yield results as they complete, using `asyncio.as_completed()` instead of `asyncio.gather()`.

---

## Phase 1: Progressive Stage 1 Updates

### Overview
Change Stage 1 from batch mode (wait for all) to progressive mode (yield as each completes).

### Changes Required:

#### 1. Backend: Convert to Async Generator
**File**: `backend/council.py`

**Change 1a**: Create new async generator function (add after line 855)

```python
async def stage1_collect_responses_with_code_progressive(
    user_query: str,
    csv_info: dict
):
    """
    Stage 1 with code execution - PROGRESSIVE VERSION.
    Yields each model's result as soon as it completes.

    Yields:
        Dict with model, code, execution_result, response, retries
    """
    import asyncio

    async def process_model_with_retry(model: str) -> Optional[Dict[str, Any]]:
        """Process a single model with retry logic on execution failure."""
        # (Same implementation as existing process_model_with_retry at lines 773-846)
        # Build the base code generation prompt
        base_prompt = CODE_GENERATION_PROMPT.format(
            filename=csv_info['filename'],
            row_count=csv_info['row_count'],
            columns=', '.join(csv_info['columns']),
            preview=csv_info['preview'],
            user_query=user_query
        )

        # Add model-specific hints if available
        model_hints = MODEL_CODE_HINTS.get(model, "")
        if model_hints:
            prompt = base_prompt + "\n" + model_hints
        else:
            prompt = base_prompt

        messages = [{"role": "user", "content": prompt}]

        # Initial code generation
        response = await query_model(model, messages, timeout=120.0)

        if response is None:
            return None

        raw_content = response.get('content', '')
        code = extract_code_from_response(raw_content)

        # Execute the code
        exec_result = await execute_with_fallback(code, csv_info['file_path'])

        # Retry logic
        retry_count = 0
        while not exec_result['success'] and retry_count < MAX_CODE_RETRIES:
            retry_count += 1
            print(f"[{model}] Code execution failed, retry {retry_count}/{MAX_CODE_RETRIES}")

            error_msg = '\n'.join(exec_result.get('errors', ['Unknown error']))[:1500]
            retry_prompt = CODE_ERROR_RETRY_PROMPT.format(
                error_message=error_msg,
                user_query=user_query,
                columns=', '.join(csv_info['columns'])
            )

            retry_messages = messages + [
                {"role": "assistant", "content": f"```python\n{code}\n```"},
                {"role": "user", "content": retry_prompt}
            ]

            retry_response = await query_model(model, retry_messages, timeout=120.0)

            if retry_response is None:
                break

            retry_content = retry_response.get('content', '')
            code = extract_code_from_response(retry_content)
            exec_result = await execute_with_fallback(code, csv_info['file_path'])

        formatted_response = format_code_execution_result(code, exec_result)

        return {
            "model": model,
            "code": code,
            "execution_result": exec_result,
            "response": formatted_response,
            "retries": retry_count
        }

    # Create tasks for all models
    tasks = {
        asyncio.create_task(process_model_with_retry(model)): model
        for model in COUNCIL_MODELS
    }

    # Yield results as each task completes
    for completed_task in asyncio.as_completed(tasks.keys()):
        result = await completed_task
        if result is not None:
            yield result
```

**Change 1b**: Update imports at top of file (line 5)
```python
from typing import List, Dict, Any, Tuple, Optional, AsyncGenerator
```

#### 2. Backend: Update Streaming Endpoint
**File**: `backend/main.py`

**Change 2a**: Update imports (add to line 15-24)
```python
from .council import (
    run_full_council,
    generate_conversation_title,
    stage1_collect_responses,
    stage1_collect_responses_with_code,
    stage1_collect_responses_with_code_progressive,  # NEW
    stage2_collect_rankings,
    stage3_synthesize_final,
    calculate_aggregate_rankings,
    E2B_ENABLED
)
```

**Change 2b**: Modify CSV streaming endpoint (replace lines 255-265)
```python
            if csv_info:
                # Use PROGRESSIVE code execution flow for CSV analysis
                yield f"data: {json.dumps({'type': 'stage1_start', 'mode': 'code_execution', 'e2b_enabled': E2B_ENABLED, 'model_count': len(COUNCIL_MODELS)})}\n\n"

                stage1_results = []
                async for result in stage1_collect_responses_with_code_progressive(content, csv_info):
                    # Yield each model's result immediately
                    stage1_results.append(result)
                    yield f"data: {json.dumps({'type': 'stage1_model_complete', 'data': result, 'completed_count': len(stage1_results), 'total_count': len(COUNCIL_MODELS)})}\n\n"

                yield f"data: {json.dumps({'type': 'stage1_complete', 'data': stage1_results})}\n\n"
```

#### 3. Frontend: Handle Progressive Events
**File**: `frontend/src/App.jsx`

**Change 3a**: Initialize stage1 as empty array in assistant message (modify lines 76-87)
```javascript
      // Create a partial assistant message that will be updated progressively
      const assistantMessage = {
        role: 'assistant',
        stage1: [],  // Start as empty array, not null
        stage2: null,
        stage3: null,
        metadata: null,
        loading: {
          stage1: false,
          stage2: false,
          stage3: false,
        },
        pendingModels: 0,  // Track how many models are still pending
      };
```

**Change 3b**: Add handler for `stage1_model_complete` event (add after line 119)
```javascript
          case 'stage1_model_complete':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              // Append this model's result to the stage1 array
              lastMsg.stage1 = [...(lastMsg.stage1 || []), event.data];
              lastMsg.pendingModels = event.total_count - event.completed_count;
              return { ...prev, messages };
            });
            break;
```

**Change 3c**: Update `stage1_start` handler to track pending models (modify lines 102-109)
```javascript
          case 'stage1_start':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              lastMsg.loading.stage1 = true;
              lastMsg.stage1 = [];  // Reset to empty array
              lastMsg.pendingModels = event.model_count || 4;  // Track pending
              return { ...prev, messages };
            });
            break;
```

#### 4. Frontend: Update Stage1 Component
**File**: `frontend/src/components/Stage1.jsx`

**Change 4a**: Add loading indicator for pending models (modify lines 20-80)
```jsx
const Stage1 = memo(function Stage1({ responses, pendingModels = 0 }) {
  const [activeTab, setActiveTab] = useState(0);

  // Memoize tab click handler
  const handleTabClick = useCallback((index) => {
    setActiveTab(index);
  }, []);

  // Show loading state if no responses yet
  if (!responses || responses.length === 0) {
    if (pendingModels > 0) {
      return (
        <div className="stage stage1">
          <h3 className="stage-title">Stage 1: Individual Responses</h3>
          <div className="stage1-loading">
            <div className="spinner"></div>
            <p>Waiting for model responses... ({pendingModels} pending)</p>
          </div>
        </div>
      );
    }
    return null;
  }

  const activeResponse = responses[activeTab];
  const hasCodeExecution = activeResponse?.code && activeResponse?.execution_result;

  return (
    <div className="stage stage1">
      <h3 className="stage-title">Stage 1: Individual Responses</h3>

      {/* Progress indicator if some models still pending */}
      {pendingModels > 0 && (
        <div className="stage1-progress">
          <span className="progress-text">
            {responses.length} of {responses.length + pendingModels} models complete
          </span>
          <div className="progress-bar">
            <div
              className="progress-fill"
              style={{ width: `${(responses.length / (responses.length + pendingModels)) * 100}%` }}
            />
          </div>
        </div>
      )}

      <div className="tabs">
        {responses.map((resp, index) => (
          <button
            key={index}
            className={`tab ${activeTab === index ? 'active' : ''} ${resp.execution_result?.success === false ? 'error' : ''}`}
            onClick={() => handleTabClick(index)}
          >
            {resp.model.split('/')[1] || resp.model}
            {resp.code && (
              <span className={`code-indicator ${resp.execution_result?.success ? 'success' : 'error'}`}>
                {resp.execution_result?.success ? ' [code]' : ' [err]'}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Rest of component unchanged... */}
```

**Change 4b**: Update ChatInterface to pass pendingModels prop
**File**: `frontend/src/components/ChatInterface.jsx`

Find where Stage1 is rendered and add the prop:
```jsx
<Stage1
  responses={msg.stage1}
  pendingModels={msg.pendingModels || 0}
/>
```

#### 5. Frontend: Add Progress Styles
**File**: `frontend/src/components/Stage1.css`

Add at end of file:
```css
/* Progressive loading styles */
.stage1-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 40px;
  color: #666;
}

.stage1-progress {
  margin-bottom: 16px;
  padding: 12px 16px;
  background: #f0f7ff;
  border-radius: 8px;
  border: 1px solid #d0e3ff;
}

.progress-text {
  font-size: 13px;
  color: #4a90e2;
  margin-bottom: 8px;
  display: block;
}

.progress-bar {
  height: 6px;
  background: #e0e0e0;
  border-radius: 3px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #4a90e2, #67a3ee);
  border-radius: 3px;
  transition: width 0.3s ease;
}
```

### Success Criteria - Phase 1:

#### Automated Verification:
- [ ] Backend starts without import errors: `python -m backend.main`
- [ ] Frontend builds without errors: `cd frontend && npm run build`
- [ ] No TypeScript/ESLint errors: `cd frontend && npm run lint`

#### Manual Verification:
- [ ] Upload CSV and submit query
- [ ] First model tab appears within ~30s (not waiting for all models)
- [ ] Progress bar shows "X of Y models complete"
- [ ] Subsequent tabs appear as each model finishes
- [ ] All results match previous batch behavior (just faster display)

---

## Phase 2: Stage 2 Sandboxed Code Execution

### Overview
Add code execution capability to Stage 2 peer review. Models can:
1. Write verification code to test Stage 1 claims
2. Generate alternative analyses
3. Both, at their discretion

### Changes Required:

#### 1. Backend: New Stage 2 Prompt
**File**: `backend/council.py`

**Change 1a**: Add new prompt constant (add after line 221)
```python
# Prompt template for Stage 2 with code execution capability
STAGE2_CODE_RANKING_PROMPT = """You are evaluating data analysis responses from other AI models. You have access to the SAME dataset they analyzed.

**Original Question**: {user_query}

**Dataset Information**:
- Filename: {filename}
- Total Rows: {row_count}
- Columns: {columns}

**Responses to Evaluate** (anonymized):

{responses_text}

---

**YOUR TASK**:

1. **Evaluate each response** - Analyze the quality, accuracy, and insights of each response.

2. **OPTIONAL: Write verification or alternative analysis code** - You MAY write Python code to:
   - Verify statistical claims made in the responses
   - Test edge cases the original analyses might have missed
   - Generate alternative visualizations that might provide better insights
   - Run additional analyses to inform your ranking

   If you choose to write code, format it as:
   ```python
   # Your verification or alternative analysis code here
   # The DataFrame is pre-loaded as `df`
   ```

3. **Provide your final ranking** based on your evaluation (and code results if you ran any).

**FORMAT YOUR RESPONSE AS**:

## Evaluation

[Your detailed evaluation of each response]

## Verification Code (Optional)

```python
# Your code here if you want to verify claims or run alternative analyses
```

## FINAL RANKING:
1. Response X
2. Response Y
3. Response Z
...

**IMPORTANT**:
- Your ranking MUST use the format "FINAL RANKING:" followed by a numbered list
- Code execution is OPTIONAL - only write code if it adds value to your evaluation
- The DataFrame is available as `df` if you write code
"""
```

#### 2. Backend: New Stage 2 Function with Code Execution
**File**: `backend/council.py`

**Change 2a**: Add new function (add after `stage2_collect_rankings` function, around line 330)
```python
async def stage2_collect_rankings_with_code(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    csv_info: Optional[Dict[str, Any]] = None
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """
    Stage 2 with optional code execution: Models rank responses and can verify claims.

    Args:
        user_query: The original user query
        stage1_results: Results from Stage 1
        csv_info: CSV file info for code execution (optional)

    Returns:
        Tuple of (rankings list with execution results, label_to_model mapping)
    """
    # Create anonymized labels
    labels = [chr(65 + i) for i in range(len(stage1_results))]
    label_to_model = {
        f"Response {label}": result['model']
        for label, result in zip(labels, stage1_results)
    }

    # Build responses text (anonymized)
    responses_text = "\n\n".join([
        f"**Response {label}**:\n{result['response']}"
        for label, result in zip(labels, stage1_results)
    ])

    # Determine if we can offer code execution
    has_csv = csv_info is not None and 'file_path' in csv_info

    if has_csv:
        # Use code-enabled prompt
        ranking_prompt = STAGE2_CODE_RANKING_PROMPT.format(
            user_query=user_query,
            filename=csv_info.get('filename', 'data.csv'),
            row_count=csv_info.get('row_count', 'Unknown'),
            columns=', '.join(csv_info.get('columns', [])),
            responses_text=responses_text
        )
    else:
        # Use original text-only prompt (existing code at lines 280-309)
        ranking_prompt = f"""You are evaluating different responses to the following question:

Question: {user_query}

Here are the responses from different models (anonymized):

{responses_text}

Your task:
1. First, evaluate each response individually. For each response, explain what it does well and what it does poorly.
2. Then, at the very end of your response, provide a final ranking.

IMPORTANT: Your final ranking MUST be formatted EXACTLY as follows:
- Start with the line "FINAL RANKING:" (all caps, with colon)
- Then list the responses from best to worst as a numbered list
- Each line should be: number, period, space, then ONLY the response label (e.g., "1. Response A")

Now provide your evaluation and ranking:"""

    messages = [{"role": "user", "content": ranking_prompt}]

    async def process_ranking_model(model: str) -> Optional[Dict[str, Any]]:
        """Process a single model's ranking, optionally executing verification code."""
        response = await query_model(model, messages, timeout=120.0)

        if response is None:
            return None

        full_text = response.get('content', '')
        parsed = parse_ranking_from_text(full_text)

        result = {
            "model": model,
            "ranking": full_text,
            "parsed_ranking": parsed,
            "execution_result": None  # Will be populated if code found
        }

        # Check if model included verification code
        if has_csv:
            code = extract_code_from_response(full_text)
            # Only execute if we extracted actual code (not the whole response)
            if code and code != full_text.strip() and len(code) > 20:
                try:
                    exec_result = await execute_with_fallback(code, csv_info['file_path'])
                    result["execution_result"] = exec_result
                    result["verification_code"] = code
                except Exception as e:
                    result["execution_result"] = {
                        "success": False,
                        "errors": [str(e)],
                        "stdout": "",
                        "images": []
                    }

        return result

    # Process all models in parallel
    import asyncio
    tasks = [process_ranking_model(model) for model in COUNCIL_MODELS]
    results = await asyncio.gather(*tasks)

    # Filter out None results
    stage2_results = [r for r in results if r is not None]

    return stage2_results, label_to_model
```

#### 3. Backend: Progressive Stage 2 Generator
**File**: `backend/council.py`

**Change 3a**: Add progressive version (add after the above function)
```python
async def stage2_collect_rankings_with_code_progressive(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    csv_info: Optional[Dict[str, Any]] = None
):
    """
    Stage 2 with code execution - PROGRESSIVE VERSION.
    Yields each model's ranking as soon as it completes.
    """
    import asyncio

    # Create anonymized labels
    labels = [chr(65 + i) for i in range(len(stage1_results))]
    label_to_model = {
        f"Response {label}": result['model']
        for label, result in zip(labels, stage1_results)
    }

    # Build responses text
    responses_text = "\n\n".join([
        f"**Response {label}**:\n{result['response']}"
        for label, result in zip(labels, stage1_results)
    ])

    has_csv = csv_info is not None and 'file_path' in csv_info

    if has_csv:
        ranking_prompt = STAGE2_CODE_RANKING_PROMPT.format(
            user_query=user_query,
            filename=csv_info.get('filename', 'data.csv'),
            row_count=csv_info.get('row_count', 'Unknown'),
            columns=', '.join(csv_info.get('columns', [])),
            responses_text=responses_text
        )
    else:
        ranking_prompt = f"""You are evaluating different responses to the following question:

Question: {user_query}

Here are the responses from different models (anonymized):

{responses_text}

Your task:
1. Evaluate each response individually.
2. Provide a final ranking with "FINAL RANKING:" header.

Now provide your evaluation and ranking:"""

    messages = [{"role": "user", "content": ranking_prompt}]

    async def process_ranking_model(model: str) -> Optional[Dict[str, Any]]:
        response = await query_model(model, messages, timeout=120.0)

        if response is None:
            return None

        full_text = response.get('content', '')
        parsed = parse_ranking_from_text(full_text)

        result = {
            "model": model,
            "ranking": full_text,
            "parsed_ranking": parsed,
            "execution_result": None
        }

        if has_csv:
            code = extract_code_from_response(full_text)
            if code and code != full_text.strip() and len(code) > 20:
                try:
                    exec_result = await execute_with_fallback(code, csv_info['file_path'])
                    result["execution_result"] = exec_result
                    result["verification_code"] = code
                except Exception as e:
                    result["execution_result"] = {
                        "success": False,
                        "errors": [str(e)],
                        "stdout": "",
                        "images": []
                    }

        return result

    # Create tasks
    tasks = {
        asyncio.create_task(process_ranking_model(model)): model
        for model in COUNCIL_MODELS
    }

    # Yield label_to_model first so frontend has it
    yield {"type": "label_to_model", "data": label_to_model}

    # Yield results as each completes
    for completed_task in asyncio.as_completed(tasks.keys()):
        result = await completed_task
        if result is not None:
            yield {"type": "ranking", "data": result}
```

#### 4. Backend: Update Streaming Endpoint for Stage 2
**File**: `backend/main.py`

**Change 4a**: Update imports
```python
from .council import (
    # ... existing imports ...
    stage2_collect_rankings_with_code_progressive,  # NEW
)
```

**Change 4b**: Modify CSV streaming endpoint Stage 2 section (replace lines 272-276)
```python
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
```

#### 5. Frontend: Handle Progressive Stage 2 Events
**File**: `frontend/src/App.jsx`

**Change 5a**: Initialize stage2 as empty array (update assistant message creation)
```javascript
      const assistantMessage = {
        role: 'assistant',
        stage1: [],
        stage2: [],  // Start as empty array
        stage3: null,
        metadata: null,
        loading: {
          stage1: false,
          stage2: false,
          stage3: false,
        },
        pendingModels: 0,
        pendingStage2Models: 0,  // Track Stage 2 pending
      };
```

**Change 5b**: Add handler for `stage2_model_complete` (add after stage2_start handler)
```javascript
          case 'stage2_model_complete':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              lastMsg.stage2 = [...(lastMsg.stage2 || []), event.data];
              lastMsg.pendingStage2Models = event.total_count - event.completed_count;
              return { ...prev, messages };
            });
            break;
```

**Change 5c**: Update `stage2_start` handler
```javascript
          case 'stage2_start':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              lastMsg.loading.stage2 = true;
              lastMsg.stage2 = [];
              lastMsg.pendingStage2Models = event.model_count || 4;
              return { ...prev, messages };
            });
            break;
```

#### 6. Frontend: Update Stage2 Component for Code Execution
**File**: `frontend/src/components/Stage2.jsx`

**Change 6a**: Add code execution display (modify component)
```jsx
import { useState, memo, useCallback, useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import './Stage2.css';

function deAnonymizeText(text, labelToModel) {
  if (!labelToModel) return text;
  let result = text;
  Object.entries(labelToModel).forEach(([label, model]) => {
    const modelShortName = model.split('/')[1] || model;
    result = result.replace(new RegExp(label, 'g'), `**${modelShortName}**`);
  });
  return result;
}

const Stage2 = memo(function Stage2({ rankings, labelToModel, aggregateRankings, pendingModels = 0 }) {
  const [activeTab, setActiveTab] = useState(0);
  const [showCode, setShowCode] = useState(false);

  const handleTabClick = useCallback((index) => {
    setActiveTab(index);
    setShowCode(false);  // Reset code view when switching tabs
  }, []);

  const deAnonymizedText = useMemo(() => {
    if (!rankings || rankings.length === 0) return '';
    return deAnonymizeText(rankings[activeTab]?.ranking || '', labelToModel);
  }, [rankings, activeTab, labelToModel]);

  // Show loading state if no rankings yet
  if (!rankings || rankings.length === 0) {
    if (pendingModels > 0) {
      return (
        <div className="stage stage2">
          <h3 className="stage-title">Stage 2: Peer Rankings</h3>
          <div className="stage2-loading">
            <div className="spinner"></div>
            <p>Models are evaluating responses... ({pendingModels} pending)</p>
          </div>
        </div>
      );
    }
    return null;
  }

  const activeRanking = rankings[activeTab];
  const hasVerificationCode = activeRanking?.verification_code && activeRanking?.execution_result;

  return (
    <div className="stage stage2">
      <h3 className="stage-title">Stage 2: Peer Rankings</h3>

      {/* Progress indicator */}
      {pendingModels > 0 && (
        <div className="stage2-progress">
          <span className="progress-text">
            {rankings.length} of {rankings.length + pendingModels} evaluations complete
          </span>
          <div className="progress-bar">
            <div
              className="progress-fill"
              style={{ width: `${(rankings.length / (rankings.length + pendingModels)) * 100}%` }}
            />
          </div>
        </div>
      )}

      <h4>Raw Evaluations</h4>
      <p className="stage-description">
        Each model evaluated all responses and provided rankings.
        {hasVerificationCode && " Some models ran verification code to validate claims."}
      </p>

      <div className="tabs">
        {rankings.map((rank, index) => (
          <button
            key={index}
            className={`tab ${activeTab === index ? 'active' : ''} ${rank.execution_result ? 'has-code' : ''}`}
            onClick={() => handleTabClick(index)}
          >
            {rank.model.split('/')[1] || rank.model}
            {rank.execution_result && (
              <span className={`code-indicator ${rank.execution_result.success ? 'success' : 'error'}`}>
                {rank.execution_result.success ? ' [verified]' : ' [code err]'}
              </span>
            )}
          </button>
        ))}
      </div>

      <div className="tab-content">
        <div className="ranking-model">{activeRanking.model}</div>

        <div className="ranking-content markdown-content">
          <ReactMarkdown>{deAnonymizedText}</ReactMarkdown>
        </div>

        {/* Verification Code Section */}
        {hasVerificationCode && (
          <div className="verification-section">
            <button
              className="toggle-code-btn"
              onClick={() => setShowCode(!showCode)}
            >
              {showCode ? 'Hide' : 'Show'} Verification Code
              {activeRanking.execution_result.success ? ' (Success)' : ' (Failed)'}
            </button>

            {showCode && (
              <div className="verification-code-block">
                <div className="code-header">Verification Code:</div>
                <pre className="code-block">
                  <code>{activeRanking.verification_code}</code>
                </pre>

                <div className="output-header">
                  {activeRanking.execution_result.success ? 'Output:' : 'Error:'}
                </div>
                <pre className={`output-block ${activeRanking.execution_result.success ? '' : 'error'}`}>
                  {activeRanking.execution_result.success
                    ? activeRanking.execution_result.stdout || '(No output)'
                    : activeRanking.execution_result.errors?.join('\n') || 'Unknown error'
                  }
                </pre>

                {activeRanking.execution_result.images?.map((img, i) => (
                  <img
                    key={i}
                    src={`http://localhost:8001/outputs/${img.split('/').pop()}`}
                    alt={`Verification plot ${i + 1}`}
                    className="verification-plot"
                  />
                ))}
              </div>
            )}
          </div>
        )}

        {/* Parsed Ranking */}
        {activeRanking.parsed_ranking && activeRanking.parsed_ranking.length > 0 && (
          <div className="parsed-ranking">
            <strong>Extracted Ranking:</strong>
            <ol>
              {activeRanking.parsed_ranking.map((label, i) => (
                <li key={i}>
                  {labelToModel && labelToModel[label]
                    ? labelToModel[label].split('/')[1] || labelToModel[label]
                    : label}
                </li>
              ))}
            </ol>
          </div>
        )}
      </div>

      {/* Aggregate Rankings - unchanged */}
      {aggregateRankings && aggregateRankings.length > 0 && (
        <div className="aggregate-rankings">
          <h4>Aggregate Rankings (Street Cred)</h4>
          <p className="stage-description">
            Combined results across all peer evaluations (lower score is better):
          </p>
          <div className="aggregate-list">
            {aggregateRankings.map((agg, index) => (
              <div key={index} className="aggregate-item">
                <span className="rank-position">#{index + 1}</span>
                <span className="rank-model">
                  {agg.model.split('/')[1] || agg.model}
                </span>
                <span className="rank-score">
                  Avg: {agg.average_rank.toFixed(2)}
                </span>
                <span className="rank-count">
                  ({agg.rankings_count} votes)
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
});

export default Stage2;
```

#### 7. Frontend: Add Stage2 Code Execution Styles
**File**: `frontend/src/components/Stage2.css`

Add at end of file:
```css
/* Progressive loading styles */
.stage2-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 40px;
  color: #666;
}

.stage2-progress {
  margin-bottom: 16px;
  padding: 12px 16px;
  background: #fff8e6;
  border-radius: 8px;
  border: 1px solid #ffe0a0;
}

.stage2-progress .progress-text {
  font-size: 13px;
  color: #b88a00;
  margin-bottom: 8px;
  display: block;
}

.stage2-progress .progress-bar {
  height: 6px;
  background: #e0e0e0;
  border-radius: 3px;
  overflow: hidden;
}

.stage2-progress .progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #f5a623, #f7b84b);
  border-radius: 3px;
  transition: width 0.3s ease;
}

/* Verification code section */
.verification-section {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px dashed #e0e0e0;
}

.toggle-code-btn {
  background: #f5f5f5;
  border: 1px solid #ddd;
  padding: 8px 16px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  color: #555;
  transition: all 0.2s ease;
}

.toggle-code-btn:hover {
  background: #e8e8e8;
  border-color: #ccc;
}

.verification-code-block {
  margin-top: 12px;
  background: #fafafa;
  border-radius: 8px;
  padding: 16px;
  border: 1px solid #e0e0e0;
}

.verification-code-block .code-header,
.verification-code-block .output-header {
  font-size: 12px;
  font-weight: 600;
  color: #666;
  margin-bottom: 8px;
  text-transform: uppercase;
}

.verification-code-block .code-block {
  background: #2d2d2d;
  color: #f8f8f2;
  padding: 12px;
  border-radius: 6px;
  overflow-x: auto;
  font-family: 'Fira Code', monospace;
  font-size: 13px;
  margin-bottom: 16px;
}

.verification-code-block .output-block {
  background: #f0f0f0;
  padding: 12px;
  border-radius: 6px;
  font-family: monospace;
  font-size: 13px;
  white-space: pre-wrap;
}

.verification-code-block .output-block.error {
  background: #fff0f0;
  color: #c00;
  border: 1px solid #ffcccc;
}

.verification-plot {
  max-width: 100%;
  margin-top: 12px;
  border-radius: 6px;
  border: 1px solid #e0e0e0;
}

/* Tab indicator for verification code */
.tab.has-code {
  border-bottom: 2px solid #f5a623;
}

.tab .code-indicator.success {
  color: #28a745;
}

.tab .code-indicator.error {
  color: #dc3545;
}
```

#### 8. Frontend: Update ChatInterface to Pass Props
**File**: `frontend/src/components/ChatInterface.jsx`

Update Stage2 rendering:
```jsx
<Stage2
  rankings={msg.stage2}
  labelToModel={msg.metadata?.label_to_model}
  aggregateRankings={msg.metadata?.aggregate_rankings}
  pendingModels={msg.pendingStage2Models || 0}
/>
```

### Success Criteria - Phase 2:

#### Automated Verification:
- [ ] Backend starts without errors: `python -m backend.main`
- [ ] Frontend builds: `cd frontend && npm run build`
- [ ] No lint errors: `cd frontend && npm run lint`

#### Manual Verification:
- [ ] Upload CSV and submit query
- [ ] Stage 2 tabs appear progressively as each model finishes evaluating
- [ ] Some models show "[verified]" badge when they include verification code
- [ ] Clicking "Show Verification Code" displays the code and output
- [ ] Verification plots display correctly
- [ ] Aggregate rankings still calculate correctly

---

## Phase 3: Apply Progressive Pattern to Text-Only Flow

### Overview
Apply the same progressive streaming to the non-CSV (text-only) endpoints for consistency.

### Changes Required:

This is a straightforward extension of Phase 1 patterns to the text-only paths:
- Create `stage1_collect_responses_progressive()` async generator
- Create `stage2_collect_rankings_progressive()` async generator
- Update `/message/stream` endpoint to use progressive generators
- Frontend already handles the events from Phases 1-2

### Implementation follows same patterns as Phases 1-2 (omitted for brevity)

### Success Criteria - Phase 3:

#### Manual Verification:
- [ ] Text-only queries (no CSV) also show progressive Stage 1 results
- [ ] Text-only queries show progressive Stage 2 results
- [ ] Behavior matches CSV flow for consistency

---

## Testing Strategy

### Unit Tests:
- Test `asyncio.as_completed()` yields in correct order
- Test ranking parsing with new verification code format
- Test code extraction from Stage 2 responses

### Integration Tests:
- Full flow with CSV upload
- Full flow without CSV (text-only)
- Error handling when model fails mid-stream

### Manual Testing Steps:
1. Start backend: `python -m backend.main`
2. Start frontend: `cd frontend && npm run dev`
3. Create new conversation
4. Upload CSV file and submit analysis query
5. Verify first Stage 1 result appears within ~30s
6. Verify progress bar updates as models complete
7. Verify Stage 2 shows verification code for some models
8. Verify all aggregate rankings calculate correctly
9. Verify Stage 3 report renders correctly

## Performance Considerations

- Progressive streaming reduces perceived latency by 50-80% (first result in ~30s vs ~120s)
- Memory usage unchanged (still collecting all results before Stage 2)
- No additional API calls (same total queries, different timing)
- Frontend updates are incremental (React's diffing handles efficiently)

## References

- Original research: `thoughts/shared/research/2025-12-27-codebase-architecture-documentation.md`
- Current Stage 1 batch code: `backend/council.py:849-850`
- Current Stage 2 code: `backend/council.py:251-328`
- SSE streaming endpoint: `backend/main.py:222-311`
