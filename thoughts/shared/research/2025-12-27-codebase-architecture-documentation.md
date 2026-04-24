---
date: 2025-12-27T23:16:48+05:30
researcher: biswajitmondal
git_commit: 92e1fccb1bdcf1bab7221aa9ed90f9dc72529131
branch: master
repository: llm-council
topic: "Comprehensive Codebase Architecture Documentation"
tags: [research, codebase, architecture, backend, frontend, storage, configuration]
status: complete
last_updated: 2025-12-27
last_updated_by: biswajitmondal
---

# Research: Comprehensive Codebase Architecture Documentation

**Date**: 2025-12-27T23:16:48+05:30
**Researcher**: biswajitmondal
**Git Commit**: 92e1fccb1bdcf1bab7221aa9ed90f9dc72529131
**Branch**: master
**Repository**: llm-council

## Research Question
Document the complete architecture of the LLM Council codebase, including all components, patterns, and infrastructure.

## Summary

The LLM Council is a 3-stage deliberation system where multiple LLMs collaboratively answer user questions. The architecture consists of:

- **Backend**: FastAPI Python application with 9 modules handling API endpoints, model orchestration, code execution, and storage
- **Frontend**: React 19 SPA with 6 components for conversation management and staged result display
- **Storage**: Supabase PostgreSQL for persistence, local filesystem for CSV uploads and plot outputs
- **Code Execution**: Dual executor system with E2B cloud sandboxes and local Jupyter fallback

## Detailed Findings

### Backend Architecture

**Location**: `/Users/biswajitmondal/Developer/llm-council/backend/`

**9 Python Modules**:

| File | Purpose | Key Functions |
|------|---------|---------------|
| `__init__.py` | Package initializer | Empty |
| `config.py` | Environment variables, model lists, paths | Loads OPENROUTER_API_KEY, SUPABASE_URL, E2B_API_KEY |
| `openrouter.py` | HTTP client for OpenRouter API | `query_model()`, `query_models_parallel()`, `build_vision_message()` |
| `council.py` | 3-stage orchestration logic | `stage1_collect_responses()`, `stage2_collect_rankings()`, `stage3_synthesize_final()` |
| `main.py` | FastAPI app with endpoints | 6 endpoints for conversations/messages, SSE streaming |
| `storage.py` | Supabase database client | CRUD operations for conversations and messages |
| `csv_processor.py` | CSV file handling | `process_csv()`, `store_full_csv()` |
| `code_executor.py` | Local Jupyter kernel execution | `execute_code_for_model()`, `validate_code()` |
| `e2b_executor.py` | E2B cloud sandbox execution | `execute_code_in_sandbox()`, `execute_code_for_model_e2b()` |

**Model Configuration** (`config.py:16-29`):
- Council models: google/gemini-3-flash-preview, deepseek/deepseek-v3.2-speciale, z-ai/glm-4.7, minimax/minimax-m2.1
- Chairman model (text): moonshotai/kimi-k2-thinking
- Vision chairman model: google/gemini-3-flash-preview

**API Endpoints** (`main.py`):
- `GET /` - Health check
- `GET /api/conversations` - List all conversations
- `POST /api/conversations` - Create new conversation
- `GET /api/conversations/{id}` - Get single conversation
- `POST /api/conversations/{id}/message/stream` - Text message with SSE
- `POST /api/conversations/{id}/message/with-csv/stream` - CSV upload with SSE

### Frontend Architecture

**Location**: `/Users/biswajitmondal/Developer/llm-council/frontend/src/`

**6 React Components**:

| Component | File | Purpose |
|-----------|------|---------|
| App | `App.jsx` | Top-level state management, conversation orchestration |
| Sidebar | `components/Sidebar.jsx` | Conversation list navigation |
| ChatInterface | `components/ChatInterface.jsx` | Message display, input form, file upload |
| Stage1 | `components/Stage1.jsx` | Tabbed view of individual model responses |
| Stage2 | `components/Stage2.jsx` | Peer rankings with de-anonymization |
| Stage3 | `components/Stage3.jsx` | Chairman synthesis with embedded visualizations |

**API Client** (`api.js`):
- Base URL: `http://localhost:8001`
- Streaming via Server-Sent Events (SSE)
- FormData upload for CSV files

**Key Patterns**:
- React hooks: `useState`, `useEffect`, `useCallback`, `useMemo`, `useRef`
- Component memoization with `memo()`
- Optimistic UI updates with rollback on error
- Progressive loading via SSE events

### Storage Architecture

**Primary**: Supabase PostgreSQL

**Schema** (`backend/supabase_schema.sql`):
```sql
-- conversations table
id (UUID, PK), created_at (TIMESTAMPTZ), title (TEXT)

-- messages table
id (UUID, PK), conversation_id (FK), role (TEXT), content (TEXT),
file_info (JSONB), stage1 (JSONB), stage2 (JSONB), stage3 (JSONB), created_at
```

**Local Filesystem**:
- `/data/conversations/` - Legacy JSON files (no longer actively used)
- `/data/uploads/` - Uploaded CSV files for code execution
- `/data/outputs/` - Generated plot PNG files

**Data Not Persisted**:
- `label_to_model` mapping (ephemeral, returned in API response only)
- `aggregate_rankings` (ephemeral, returned in API response only)

### Code Execution System

**Dual Executor Pattern** (`council.py:24-54`):

1. **E2B Cloud Sandbox** (primary when enabled):
   - Cloud-based isolated execution
   - 60-second timeout per sandbox
   - Controlled by `E2B_ENABLED` environment variable

2. **Local Jupyter Kernel** (fallback):
   - Uses `jupyter_client.KernelManager`
   - Security validation: blocked imports (os, sys, subprocess, etc.)
   - Blocked patterns: `open()`, `exec()`, `eval()`, dunder methods

**Fallback Logic**:
- Tries E2B first if enabled
- Falls back to local on infrastructure errors (API key issues, sandbox errors, timeouts)
- Does NOT fall back on code execution errors (lets retry loop handle those)

**Retry System** (`council.py:806-836`):
- Maximum 2 retries per model
- Conversational retry: shows failed code + error to model
- Error messages truncated to 1500 characters

### Configuration System

**Environment Variables** (from `.env.example`):

| Variable | Purpose | Required |
|----------|---------|----------|
| `OPENROUTER_API_KEY` | LLM API access | Yes |
| `SUPABASE_URL` | Database URL | Yes |
| `SUPABASE_KEY` | Database auth | Yes |
| `E2B_API_KEY` | Cloud execution | No |
| `E2B_ENABLED` | Toggle E2B | No (default: true) |

**Hardcoded Values**:
- Backend port: 8001
- CORS origins: localhost:5173, localhost:5175, localhost:3000
- Max CSV size: 5MB
- E2B timeout: 60 seconds
- Code retry limit: 2

### Error Handling Patterns

**21 distinct patterns documented across codebase**:

**Backend Patterns**:
1. Silent graceful degradation (return `None` on failure)
2. Image encoding error suppression (continue with partial data)
3. Explicit `FileNotFoundError` raising
4. Conditional import with try/except
5. Fallback execution with infrastructure error detection
6. Retry loop with LLM error feedback
7. Empty results check
8. FastAPI `HTTPException` for 404s
9. SSE error events
10. Validation with `ValueError`
11. Code security validation (tuple returns)
12. Try/finally for resource cleanup
13. Deadline-based timeout handling
14. E2B sandbox error handling with cleanup
15. Database exception handling

**Frontend Patterns**:
16. Try/catch with console.error logging
17. Optimistic UI with rollback on error
18. SSE event error handling
19. API response validation (`response.ok` check)
20. SSE parse error handling (per-event try/catch)
21. Fetch error with JSON fallback

### Testing Infrastructure

**Current State**: No formal test infrastructure exists.

**Not Present**:
- Test files (*test*.py, *.test.js, *.spec.js)
- Test directories (tests/, __tests__/)
- Test configuration (pytest.ini, jest.config.js)
- Testing dependencies in requirements.txt or package.json

**Documentation Reference**:
- CLAUDE.md mentions `test_openrouter.py` for API connectivity testing
- This file does not currently exist in the repository

## Code References

### Backend Core Files
- `backend/config.py:16-29` - Model configuration
- `backend/openrouter.py:79-125` - Model query function
- `backend/council.py:224-248` - Stage 1 collection
- `backend/council.py:251-328` - Stage 2 anonymized ranking
- `backend/council.py:331-360` - Stage 3 routing
- `backend/main.py:151-219` - Streaming endpoint
- `backend/storage.py:41-85` - Conversation retrieval
- `backend/code_executor.py:39-61` - Security validation
- `backend/e2b_executor.py:20-164` - Cloud execution

### Frontend Core Files
- `frontend/src/App.jsx:60-189` - Message sending with streaming
- `frontend/src/api.js:76-114` - SSE streaming client
- `frontend/src/components/Stage1.jsx:44-73` - Code execution display
- `frontend/src/components/Stage2.jsx:6-16` - De-anonymization function
- `frontend/src/components/Stage3.jsx:9-89` - Report with visualizations

### Configuration Files
- `backend/.env.example` - Environment variable template
- `backend/requirements.txt` - Python dependencies
- `frontend/package.json` - Node.js dependencies
- `backend/supabase_schema.sql` - Database schema

## Architecture Documentation

### 3-Stage Deliberation Flow

```
User Query
    ↓
Stage 1: Parallel queries to 4 council models
    ↓ (For CSV: models generate + execute Python code)
Stage 2: Anonymize responses → Parallel ranking queries
    ↓ Responses labeled as "Response A", "Response B", etc.
    ↓ Each model evaluates and ranks all responses
Stage 3: Chairman synthesizes final answer
    ↓ (For CSV: writes report referencing Stage 1 visualizations)
Return: {stage1, stage2, stage3, metadata}
```

### Data Flow: API to Storage

```
Frontend (App.jsx)
    ↓ SSE stream
API Endpoint (main.py)
    ↓
Council Orchestration (council.py)
    ↓
OpenRouter API (openrouter.py) ←→ Code Execution (code_executor.py / e2b_executor.py)
    ↓
Supabase Storage (storage.py)
    ↓
PostgreSQL Database
```

### File Storage Flow

```
User uploads CSV
    ↓
CSVProcessor.store_full_csv() → /data/uploads/<uuid>_<filename>.csv
    ↓
Code execution generates plots → /data/outputs/plot_<hash>.png
    ↓
FastAPI serves static files → GET /outputs/<filename>
    ↓
Frontend displays images via <img src="http://localhost:8001/outputs/...">
```

## Related Research

No other research documents exist in `thoughts/shared/research/` - this is the first.

## Open Questions

1. **Legacy JSON Storage**: Files exist in `/data/conversations/` from pre-Supabase implementation. What is the migration/cleanup plan?

2. **File Cleanup**: No mechanism exists to clean up `/data/uploads/` or `/data/outputs/`. Files accumulate indefinitely.

3. **Ephemeral Metadata**: `label_to_model` and `aggregate_rankings` are not persisted. Is this intentional, or should they be stored for historical analysis?

4. **Test Coverage**: No automated tests exist. What testing strategy is planned?

5. **Model Configuration**: Council models are hardcoded in `config.py`. CLAUDE.md mentions UI-based configuration as a future enhancement.
