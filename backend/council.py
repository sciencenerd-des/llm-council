"""3-stage LLM Council orchestration."""

import logging
import re
from typing import List, Dict, Any, Tuple, Optional, AsyncGenerator
from .openrouter import query_models_parallel, query_model, build_vision_message
from .config import COUNCIL_MODELS, CHAIRMAN_MODEL, VISION_CHAIRMAN_MODEL, E2B_ENABLED
from .code_executor import execute_code_for_model

# Setup logging
logger = logging.getLogger(__name__)

# Conditional import for E2B executor
if E2B_ENABLED:
    try:
        from .e2b_executor import execute_code_for_model_e2b
    except ImportError:
        execute_code_for_model_e2b = None
        E2B_ENABLED = False
else:
    execute_code_for_model_e2b = None


async def execute_with_fallback(code: str, csv_path: str) -> Dict[str, Any]:
    """
    Execute code with E2B if enabled, falling back to local Jupyter if E2B fails.

    Args:
        code: Python code to execute
        csv_path: Path to CSV file

    Returns:
        Dict with 'stdout', 'images', 'errors', 'success' keys
    """
    if E2B_ENABLED and execute_code_for_model_e2b:
        result = await execute_code_for_model_e2b(code, csv_path)
        if result['success']:
            return result
        # If E2B failed with API/infrastructure error (not code error), try local
        error_msgs = result.get('errors', [])
        is_infra_error = any(
            'Sandbox error' in e or
            'E2B_API_KEY' in e or
            'Failed to read CSV' in e or
            'timeout' in e.lower()
            for e in error_msgs
        )
        if is_infra_error:
            logger.warning(f"E2B failed with infrastructure error, falling back to local: {error_msgs}")
            return await execute_code_for_model(code, csv_path)
        # Code execution error - return as-is (don't retry locally)
        return result
    # E2B not enabled - use local executor
    return await execute_code_for_model(code, csv_path)


# Model-specific code generation hints to prevent common errors
# These are appended to the code generation prompt for specific models
MODEL_CODE_HINTS = {
    "minimax/minimax-m2.1": """
CRITICAL RULES FOR YOUR CODE:
1. ALWAYS check if columns exist before using them: `if 'column_name' in df.columns:`
2. ALWAYS check DataFrame is not empty: `if not df.empty:`
3. For numeric operations, use: `df.select_dtypes(include=[np.number])` to get only numeric columns
4. Before correlation/heatmap: `numeric_df = df.select_dtypes(include=[np.number])`
5. Handle NaN values: `df.dropna()` or `df.fillna(0)` before calculations
6. Use try/except around plot generation to handle edge cases
7. NEVER assume column names - always verify they exist first
8. For bar charts with categories, limit to top 10: `.head(10)` or `.nlargest(10)`
""",
    "z-ai/glm-4.7": """
IMPORTANT CODING GUIDELINES:
1. Check column existence before accessing: `if col in df.columns`
2. Use `df.select_dtypes(include='number')` for numeric-only operations
3. Handle empty DataFrames gracefully with checks
4. Wrap visualization code in try/except blocks
5. Use `.head(10)` to limit large category plots
""",
}

# Maximum retries for code execution
MAX_CODE_RETRIES = 2


# Prompt template for error feedback retry
CODE_ERROR_RETRY_PROMPT = """Your previous code execution FAILED with the following error:

```
{error_message}
```

Original task: {user_query}

Dataset columns available: {columns}

Please fix your code to handle this error. Common fixes:
1. Check if columns exist before using them
2. Use `df.select_dtypes(include=[np.number])` for numeric operations
3. Handle NaN/empty values with `.dropna()` or `.fillna()`
4. Add try/except blocks around visualization code
5. Verify data types before operations

Write the CORRECTED Python code:
```python
"""


# Prompt template for Chairman synthesis - WRITTEN REPORT (not code execution)
CHAIRMAN_SYNTHESIS_PROMPT = """You are the Chairman of an LLM Council synthesizing a DATA ANALYSIS REPORT. Multiple AI models have independently analyzed a dataset, generated code, executed it, and produced visualizations. Their work has been peer-reviewed by the council.

**Original User Question**: {user_query}

**Dataset Information**:
- Filename: {filename}
- Total Rows: {row_count}
- Columns: {columns}

**STAGE 1 - Individual Model Analyses**:
{stage1_summary}

**STAGE 2 - Peer Rankings (Consensus)**:
{stage2_summary}

**Available Visualizations from Council Models**:
{available_visualizations}

YOUR TASK AS CHAIRMAN:
Write a comprehensive **Research Report** or **Business Intelligence Report** that synthesizes the findings from all council models. You are NOT generating code - you are writing an analytical report.

REPORT STRUCTURE:
1. **Executive Summary** (2-3 paragraphs)
   - Direct answer to the user's question
   - Key findings and insights
   - Critical metrics discovered

2. **Methodology Overview**
   - Brief summary of how the council approached the analysis
   - Which models provided the most valuable insights (reference the rankings)

3. **Detailed Findings**
   - Synthesize the analytical insights from all models
   - Highlight areas of agreement and any notable differences
   - Present statistical findings clearly

4. **Key Visualizations**
   - Reference the most relevant charts from the council models
   - Use the format: [[VIZ:model_name:image_index]] to include a visualization
   - Example: [[VIZ:gpt-4o:0]] includes the first image from GPT-4o's analysis
   - Explain what each visualization shows and why it matters

5. **Conclusions & Recommendations**
   - Actionable insights based on the analysis
   - Suggested next steps or areas for deeper investigation

IMPORTANT GUIDELINES:
- Write in clear, professional prose suitable for a business or research audience
- Do NOT write any Python code - this is a written report only
- Use [[VIZ:model_name:image_index]] syntax to embed relevant visualizations
- Focus on insights and interpretation, not code mechanics
- Synthesize the BEST insights from ALL models, not just the top-ranked one

Write your comprehensive report now:"""


# Prompt template for code generation when CSV is attached
CODE_GENERATION_PROMPT = """You are a senior data analyst creating comprehensive visual dashboards. A CSV file has been uploaded:

**Filename**: {filename}
**Total Rows**: {row_count}
**Columns**: {columns}

**Preview (first 5 rows)**:
{preview}

**User Question**: {user_query}

Create a COMPREHENSIVE VISUAL ANALYSIS with multiple dashboard-style visualizations.

REQUIRED OUTPUT:
1. **Print key statistical findings** - summary statistics, correlations, patterns
2. **Create multiple visualizations** as a professional dashboard:
   - Correlation heatmap (if numeric columns exist)
   - Distribution plots for key variables
   - Category breakdowns (bar charts for categorical data)
   - Scatter plots showing relationships
   - Summary dashboard combining key insights

CODING RULES:
1. DataFrame is pre-loaded as `df`
2. Use: pandas, numpy, matplotlib, seaborn ONLY
3. Print analysis results clearly with headers
4. Use `plt.figure(figsize=(12, 8))` for readable plots
5. Use `plt.tight_layout()` before `plt.show()`
6. Create SEPARATE figures for different visualizations (not subplots)
7. Use clear titles and labels on all plots
8. Apply professional color schemes (sns.set_palette)

DASHBOARD STRUCTURE:
```python
# 1. Statistical Summary
print("=" * 60)
print("STATISTICAL SUMMARY")
print("=" * 60)
# ... analysis code ...

# 2. Correlation Heatmap
plt.figure(figsize=(12, 10))
# ... heatmap code ...
plt.show()

# 3. Distribution Analysis
plt.figure(figsize=(14, 6))
# ... distribution code ...
plt.show()

# ... more visualizations ...
```

Write ONLY executable Python code. Start immediately:
```python
"""

# Prompt template for Stage 2 with optional code execution capability
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


async def stage1_collect_responses(user_query: str) -> List[Dict[str, Any]]:
    """
    Stage 1: Collect individual responses from all council models.

    Args:
        user_query: The user's question

    Returns:
        List of dicts with 'model' and 'response' keys
    """
    messages = [{"role": "user", "content": user_query}]

    # Query all models in parallel
    responses = await query_models_parallel(COUNCIL_MODELS, messages)

    # Format results
    stage1_results = []
    for model, response in responses.items():
        if response is not None:  # Only include successful responses
            stage1_results.append({
                "model": model,
                "response": response.get('content', '')
            })

    return stage1_results


async def stage1_collect_responses_progressive(
    user_query: str
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Stage 1 for text-only queries - PROGRESSIVE VERSION.
    Yields each model's response as soon as it completes.

    Args:
        user_query: The user's question

    Yields:
        Dict with 'model' and 'response' keys
    """
    import asyncio

    messages = [{"role": "user", "content": user_query}]

    async def query_single_model(model: str) -> Optional[Dict[str, Any]]:
        """Query a single model and return formatted result."""
        response = await query_model(model, messages, timeout=120.0)
        if response is None:
            return None
        return {
            "model": model,
            "response": response.get('content', '')
        }

    # Create tasks with model tracking
    tasks = {
        asyncio.create_task(query_single_model(model)): model
        for model in COUNCIL_MODELS
    }

    # Yield results as each task completes
    for completed_task in asyncio.as_completed(tasks.keys()):
        result = await completed_task
        if result is not None:
            yield result


async def stage2_collect_rankings(
    user_query: str,
    stage1_results: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """
    Stage 2: Each model ranks the anonymized responses.

    Args:
        user_query: The original user query
        stage1_results: Results from Stage 1

    Returns:
        Tuple of (rankings list, label_to_model mapping)
    """
    # Create anonymized labels for responses (Response A, Response B, etc.)
    labels = [chr(65 + i) for i in range(len(stage1_results))]  # A, B, C, ...

    # Create mapping from label to model name
    label_to_model = {
        f"Response {label}": result['model']
        for label, result in zip(labels, stage1_results)
    }

    # Build the ranking prompt
    responses_text = "\n\n".join([
        f"Response {label}:\n{result['response']}"
        for label, result in zip(labels, stage1_results)
    ])

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
- Do not add any other text or explanations in the ranking section

Example of the correct format for your ENTIRE response:

Response A provides good detail on X but misses Y...
Response B is accurate but lacks depth on Z...
Response C offers the most comprehensive answer...

FINAL RANKING:
1. Response C
2. Response A
3. Response B

Now provide your evaluation and ranking:"""

    messages = [{"role": "user", "content": ranking_prompt}]

    # Get rankings from all council models in parallel
    responses = await query_models_parallel(COUNCIL_MODELS, messages)

    # Format results
    stage2_results = []
    for model, response in responses.items():
        if response is not None:
            full_text = response.get('content', '')
            parsed = parse_ranking_from_text(full_text)
            stage2_results.append({
                "model": model,
                "ranking": full_text,
                "parsed_ranking": parsed
            })

    return stage2_results, label_to_model


async def stage2_collect_rankings_with_code_progressive(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    csv_info: Optional[Dict[str, Any]] = None
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Stage 2 with optional code execution - PROGRESSIVE VERSION.
    Yields each model's ranking as soon as it completes.

    Args:
        user_query: The original user query
        stage1_results: Results from Stage 1
        csv_info: Optional CSV file info for code execution

    Yields:
        Dicts with type='label_to_model' or type='ranking'
    """
    import asyncio

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
        # Use original text-only prompt
        ranking_prompt = f"""You are evaluating different responses to the following question:

Question: {user_query}

Here are the responses from different models (anonymized):

{responses_text}

Your task:
1. Evaluate each response individually. For each response, explain what it does well and what it does poorly.
2. Provide a final ranking with "FINAL RANKING:" header followed by a numbered list.

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
            "execution_result": None
        }

        # Check if model included verification code (only for CSV queries)
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

    # Yield label_to_model first so frontend has it
    yield {"type": "label_to_model", "data": label_to_model}

    # Create tasks with model tracking
    tasks = {
        asyncio.create_task(process_ranking_model(model)): model
        for model in COUNCIL_MODELS
    }

    # Yield results as each task completes
    for completed_task in asyncio.as_completed(tasks.keys()):
        result = await completed_task
        if result is not None:
            yield {"type": "ranking", "data": result}


async def stage3_synthesize_final(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    stage2_results: List[Dict[str, Any]],
    csv_info: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Stage 3: Chairman synthesizes final response with visualizations.
    When CSV data is available, the Chairman generates code for a comprehensive dashboard.

    Args:
        user_query: The original user query
        stage1_results: Individual model responses from Stage 1
        stage2_results: Rankings from Stage 2
        csv_info: Optional CSV file info for code execution

    Returns:
        Dict with 'model', 'response', 'images', and execution details
    """
    # Check if this is a CSV analysis (code execution mode)
    has_csv = csv_info is not None and 'file_path' in csv_info

    if has_csv:
        # Chairman generates comprehensive synthesis with code execution
        return await _stage3_with_code_execution(
            user_query, stage1_results, stage2_results, csv_info
        )
    else:
        # Text-only synthesis
        return await _stage3_text_only(user_query, stage1_results, stage2_results)


async def _stage3_with_code_execution(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    stage2_results: List[Dict[str, Any]],
    csv_info: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Stage 3: Chairman writes a comprehensive research report synthesizing all model analyses.
    No code execution - the Chairman references visualizations from Stage 1 models.
    """
    # Build summary of Stage 1 results (focus on findings, not full code)
    stage1_summary_parts = []
    for result in stage1_results:
        model_name = result['model'].split('/')[-1]
        if result.get('execution_result', {}).get('success'):
            stdout = result['execution_result'].get('stdout', '')[:2000]  # Limit size
            stage1_summary_parts.append(f"**{model_name}** (Success):\n{stdout}")
        else:
            errors = result.get('execution_result', {}).get('errors', [])
            stage1_summary_parts.append(f"**{model_name}** (Failed): {errors[:500] if errors else 'Unknown error'}")

    stage1_summary = "\n\n".join(stage1_summary_parts)

    # Build summary of Stage 2 rankings
    stage2_summary_parts = []
    for result in stage2_results:
        model_name = result['model'].split('/')[-1]
        parsed = result.get('parsed_ranking', [])
        if parsed:
            ranking_str = " > ".join(parsed)
            stage2_summary_parts.append(f"**{model_name}**: {ranking_str}")

    stage2_summary = "\n".join(stage2_summary_parts) if stage2_summary_parts else "No rankings available"

    # Build list of available visualizations from Stage 1
    available_viz_parts = []
    stage1_images_map = {}  # model_short_name -> list of image paths
    for result in stage1_results:
        model_short_name = result['model'].split('/')[-1]
        images = result.get('execution_result', {}).get('images', [])
        if images:
            stage1_images_map[model_short_name] = images
            available_viz_parts.append(
                f"- **{model_short_name}**: {len(images)} visualization(s) available "
                f"(use [[VIZ:{model_short_name}:0]], [[VIZ:{model_short_name}:1]], etc.)"
            )

    available_visualizations = "\n".join(available_viz_parts) if available_viz_parts else "No visualizations available from Stage 1 models."

    # Build the Chairman report prompt
    chairman_prompt = CHAIRMAN_SYNTHESIS_PROMPT.format(
        user_query=user_query,
        filename=csv_info.get('filename', 'data.csv'),
        row_count=csv_info.get('row_count', 'Unknown'),
        columns=', '.join(csv_info.get('columns', [])),
        stage1_summary=stage1_summary,
        stage2_summary=stage2_summary,
        available_visualizations=available_visualizations
    )

    messages = [{"role": "user", "content": chairman_prompt}]

    # Use the vision-capable model for report writing (usually more capable)
    active_chairman = VISION_CHAIRMAN_MODEL

    # Get written report from Chairman
    response = await query_model(active_chairman, messages, timeout=180.0)

    if response is None:
        return {
            "model": active_chairman,
            "response": "Error: Unable to generate synthesis report.",
            "images": [],
            "stage1_images_map": stage1_images_map
        }

    raw_content = response.get('content', '')

    # Process the report to extract referenced visualizations and build image list
    report_content, referenced_images = _process_chairman_report(raw_content, stage1_images_map)

    return {
        "model": active_chairman,
        "response": report_content,
        "images": referenced_images,
        "stage1_images_map": stage1_images_map,
        "is_report": True  # Flag to indicate this is a written report, not code execution
    }


def _process_chairman_report(
    report_content: str,
    stage1_images_map: Dict[str, List[str]]
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Process the Chairman's written report to handle visualization references.

    Finds [[VIZ:model_name:index]] tags and:
    1. Replaces them with a structured marker for frontend rendering
    2. Builds a list of referenced images with metadata

    Args:
        report_content: The raw report text from the Chairman
        stage1_images_map: Mapping of model_short_name -> list of image paths

    Returns:
        Tuple of (processed_report_content, referenced_images_list)
    """
    import re

    referenced_images = []
    image_counter = 0

    def replace_viz_tag(match):
        nonlocal image_counter
        model_name = match.group(1)
        index_str = match.group(2)

        try:
            index = int(index_str)
        except ValueError:
            # Invalid index, leave as-is
            return match.group(0)

        # Check if model exists and has images at this index
        if model_name in stage1_images_map:
            images = stage1_images_map[model_name]
            if 0 <= index < len(images):
                image_path = images[index]
                referenced_images.append({
                    "model": model_name,
                    "index": index,
                    "path": image_path,
                    "ref_id": image_counter
                })
                # Replace with a marker that frontend can render
                marker = f"[[IMAGE_REF:{image_counter}:{model_name}]]"
                image_counter += 1
                return marker

        # Model or image not found, leave original text but note it
        return f"*(Visualization from {model_name} not available)*"

    # Pattern to match [[VIZ:model_name:index]]
    pattern = r'\[\[VIZ:([^:]+):(\d+)\]\]'
    processed_content = re.sub(pattern, replace_viz_tag, report_content)

    return processed_content, referenced_images


def _format_chairman_output(code: str, exec_result: dict, user_query: str) -> str:
    """Format the Chairman's code execution output for display."""
    parts = []

    if exec_result['success']:
        # Add executive summary from stdout
        if exec_result.get('stdout'):
            parts.append("## Executive Summary\n")
            parts.append(exec_result['stdout'])
            parts.append("\n")

        # Reference generated visualizations
        if exec_result.get('images'):
            parts.append(f"\n## Comprehensive Dashboard\n")
            parts.append(f"Generated {len(exec_result['images'])} visualization(s) synthesizing all council insights.\n")
    else:
        parts.append("## Analysis\n")
        parts.append("The Chairman encountered an error generating the comprehensive dashboard.\n")
        if exec_result.get('errors'):
            parts.append("\n**Error details:**\n```\n")
            parts.append('\n'.join(exec_result['errors'][:3]))  # Limit errors
            parts.append("\n```\n")

    return '\n'.join(parts)


async def _stage3_text_only(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    stage2_results: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Stage 3 text-only synthesis (no CSV data).
    """
    # Build comprehensive context for chairman
    stage1_text = "\n\n".join([
        f"Model: {result['model']}\nResponse: {result['response']}"
        for result in stage1_results
    ])

    stage2_text = "\n\n".join([
        f"Model: {result['model']}\nRanking: {result['ranking']}"
        for result in stage2_results
    ])

    active_chairman = CHAIRMAN_MODEL

    chairman_prompt = f"""You are the Chairman of an LLM Council. Multiple AI models have provided responses to a user's question, and then ranked each other's responses.

Original Question: {user_query}

STAGE 1 - Individual Responses:
{stage1_text}

STAGE 2 - Peer Rankings:
{stage2_text}

Your task as Chairman is to synthesize all of this information into a single, comprehensive, accurate answer to the user's original question. Consider:
- The individual responses and their insights
- The peer rankings and what they reveal about response quality
- Any patterns of agreement or disagreement

Provide a clear, well-reasoned final answer that represents the council's collective wisdom:"""

    messages = [{"role": "user", "content": chairman_prompt}]

    response = await query_model(active_chairman, messages)

    if response is None:
        return {
            "model": active_chairman,
            "response": "Error: Unable to generate final synthesis.",
            "images": []
        }

    return {
        "model": active_chairman,
        "response": response.get('content', ''),
        "images": []
    }


def parse_ranking_from_text(ranking_text: str) -> List[str]:
    """
    Parse the FINAL RANKING section from the model's response.

    Args:
        ranking_text: The full text response from the model

    Returns:
        List of response labels in ranked order
    """
    import re

    # Look for "FINAL RANKING:" section
    if "FINAL RANKING:" in ranking_text:
        # Extract everything after "FINAL RANKING:"
        parts = ranking_text.split("FINAL RANKING:")
        if len(parts) >= 2:
            ranking_section = parts[1]
            # Try to extract numbered list format (e.g., "1. Response A")
            # This pattern looks for: number, period, optional space, "Response X"
            numbered_matches = re.findall(r'\d+\.\s*Response [A-Z]', ranking_section)
            if numbered_matches:
                # Extract just the "Response X" part
                return [re.search(r'Response [A-Z]', m).group() for m in numbered_matches]

            # Fallback: Extract all "Response X" patterns in order
            matches = re.findall(r'Response [A-Z]', ranking_section)
            return matches

    # Fallback: try to find any "Response X" patterns in order
    matches = re.findall(r'Response [A-Z]', ranking_text)
    return matches


def calculate_aggregate_rankings(
    stage2_results: List[Dict[str, Any]],
    label_to_model: Dict[str, str]
) -> List[Dict[str, Any]]:
    """
    Calculate aggregate rankings across all models.

    Args:
        stage2_results: Rankings from each model (with pre-parsed rankings)
        label_to_model: Mapping from anonymous labels to model names

    Returns:
        List of dicts with model name and average rank, sorted best to worst
    """
    from collections import defaultdict

    # Track positions for each model
    model_positions = defaultdict(list)

    for ranking in stage2_results:
        # Use pre-parsed ranking if available (optimization: avoid re-parsing)
        parsed_ranking = ranking.get('parsed_ranking') or parse_ranking_from_text(ranking['ranking'])

        for position, label in enumerate(parsed_ranking, start=1):
            if label in label_to_model:
                model_name = label_to_model[label]
                model_positions[model_name].append(position)

    # Calculate average position for each model
    aggregate = []
    for model, positions in model_positions.items():
        if positions:
            avg_rank = sum(positions) / len(positions)
            aggregate.append({
                "model": model,
                "average_rank": round(avg_rank, 2),
                "rankings_count": len(positions)
            })

    # Sort by average rank (lower is better)
    aggregate.sort(key=lambda x: x['average_rank'])

    return aggregate


async def generate_conversation_title(user_query: str) -> str:
    """
    Generate a short title for a conversation based on the first user message.

    Args:
        user_query: The first user message

    Returns:
        A short title (3-5 words)
    """
    title_prompt = f"""Generate a very short title (3-5 words maximum) that summarizes the following question.
The title should be concise and descriptive. Do not use quotes or punctuation in the title.

Question: {user_query}

Title:"""

    messages = [{"role": "user", "content": title_prompt}]

    # Use gemini-2.5-flash for title generation (fast and cheap)
    response = await query_model("google/gemini-2.5-flash", messages, timeout=30.0)

    if response is None:
        # Fallback to a generic title
        return "New Conversation"

    title = response.get('content', 'New Conversation').strip()

    # Clean up the title - remove quotes, limit length
    title = title.strip('"\'')

    # Truncate if too long
    if len(title) > 50:
        title = title[:47] + "..."

    return title


def extract_code_from_response(content: str) -> str:
    """Extract Python code from model response, handling markdown blocks."""
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


async def stage1_collect_responses_with_code(
    user_query: str,
    csv_info: dict
) -> List[Dict[str, Any]]:
    """
    Stage 1 with code execution: Models write code, we execute it.
    Includes model-specific hints and retry logic for failed executions.

    Args:
        user_query: The user's question
        csv_info: Dict with file_path, filename, row_count, columns, preview

    Returns:
        List of dicts with model, code, execution_result, and response
    """
    import asyncio

    async def process_model_with_retry(model: str) -> Optional[Dict[str, Any]]:
        """Process a single model with retry logic on execution failure."""
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

        # Execute the code (with automatic fallback from E2B to local if needed)
        exec_result = await execute_with_fallback(code, csv_info['file_path'])

        # If execution failed, retry with error feedback
        retry_count = 0
        while not exec_result['success'] and retry_count < MAX_CODE_RETRIES:
            retry_count += 1
            print(f"[{model}] Code execution failed, retry {retry_count}/{MAX_CODE_RETRIES}")

            # Build error feedback prompt
            error_msg = '\n'.join(exec_result.get('errors', ['Unknown error']))[:1500]
            retry_prompt = CODE_ERROR_RETRY_PROMPT.format(
                error_message=error_msg,
                user_query=user_query,
                columns=', '.join(csv_info['columns'])
            )

            # Add the retry prompt to conversation
            retry_messages = messages + [
                {"role": "assistant", "content": f"```python\n{code}\n```"},
                {"role": "user", "content": retry_prompt}
            ]

            # Get corrected code
            retry_response = await query_model(model, retry_messages, timeout=120.0)

            if retry_response is None:
                break

            retry_content = retry_response.get('content', '')
            code = extract_code_from_response(retry_content)

            # Execute the corrected code (with automatic fallback)
            exec_result = await execute_with_fallback(code, csv_info['file_path'])

        # Build final response combining code and results
        formatted_response = format_code_execution_result(code, exec_result)

        return {
            "model": model,
            "code": code,
            "execution_result": exec_result,
            "response": formatted_response,
            "retries": retry_count
        }

    # Process all models in parallel with retry logic
    tasks = [process_model_with_retry(model) for model in COUNCIL_MODELS]
    results = await asyncio.gather(*tasks)

    # Filter out None results
    stage1_results = [r for r in results if r is not None]

    return stage1_results


async def stage1_collect_responses_with_code_progressive(
    user_query: str,
    csv_info: dict
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Stage 1 with code execution - PROGRESSIVE VERSION.
    Yields each model's result as soon as it completes.

    Args:
        user_query: The user's question
        csv_info: Dict with file_path, filename, row_count, columns, preview

    Yields:
        Dict with model, code, execution_result, response, retries
    """
    import asyncio

    async def process_model_with_retry(model: str) -> Optional[Dict[str, Any]]:
        """Process a single model with retry logic on execution failure."""
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

        # Execute the code (with automatic fallback from E2B to local if needed)
        exec_result = await execute_with_fallback(code, csv_info['file_path'])

        # If execution failed, retry with error feedback
        retry_count = 0
        while not exec_result['success'] and retry_count < MAX_CODE_RETRIES:
            retry_count += 1
            print(f"[{model}] Code execution failed, retry {retry_count}/{MAX_CODE_RETRIES}")

            # Build error feedback prompt
            error_msg = '\n'.join(exec_result.get('errors', ['Unknown error']))[:1500]
            retry_prompt = CODE_ERROR_RETRY_PROMPT.format(
                error_message=error_msg,
                user_query=user_query,
                columns=', '.join(csv_info['columns'])
            )

            # Add the retry prompt to conversation
            retry_messages = messages + [
                {"role": "assistant", "content": f"```python\n{code}\n```"},
                {"role": "user", "content": retry_prompt}
            ]

            # Get corrected code
            retry_response = await query_model(model, retry_messages, timeout=120.0)

            if retry_response is None:
                break

            retry_content = retry_response.get('content', '')
            code = extract_code_from_response(retry_content)

            # Execute the corrected code (with automatic fallback)
            exec_result = await execute_with_fallback(code, csv_info['file_path'])

        # Build final response combining code and results
        formatted_response = format_code_execution_result(code, exec_result)

        return {
            "model": model,
            "code": code,
            "execution_result": exec_result,
            "response": formatted_response,
            "retries": retry_count
        }

    # Create tasks with model tracking
    tasks = {
        asyncio.create_task(process_model_with_retry(model)): model
        for model in COUNCIL_MODELS
    }

    # Yield results as each task completes
    for completed_task in asyncio.as_completed(tasks.keys()):
        result = await completed_task
        if result is not None:
            yield result


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

    # Stage 3: Synthesize final answer (written report with Stage 1 visualizations)
    stage3_result = await stage3_synthesize_final(
        user_query,
        stage1_results,
        stage2_results,
        csv_info  # Pass csv_info to trigger report generation mode
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


async def run_full_council(user_query: str) -> Tuple[List, List, Dict, Dict]:
    """
    Run the complete 3-stage council process.

    Args:
        user_query: The user's question

    Returns:
        Tuple of (stage1_results, stage2_results, stage3_result, metadata)
    """
    # Stage 1: Collect individual responses
    stage1_results = await stage1_collect_responses(user_query)

    # If no models responded successfully, return error
    if not stage1_results:
        return [], [], {
            "model": "error",
            "response": "All models failed to respond. Please try again."
        }, {}

    # Stage 2: Collect rankings
    stage2_results, label_to_model = await stage2_collect_rankings(user_query, stage1_results)

    # Calculate aggregate rankings
    aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)

    # Stage 3: Synthesize final answer
    stage3_result = await stage3_synthesize_final(
        user_query,
        stage1_results,
        stage2_results
    )

    # Prepare metadata
    metadata = {
        "label_to_model": label_to_model,
        "aggregate_rankings": aggregate_rankings
    }

    return stage1_results, stage2_results, stage3_result, metadata
