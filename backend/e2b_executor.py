"""E2B cloud sandbox code execution for LLM Council."""

import asyncio
import base64
import logging
import time
import uuid
from pathlib import Path
from typing import Dict, Any

from .config import E2B_API_KEY, E2B_SANDBOX_TIMEOUT, OUTPUT_DIR

# Setup logging
logger = logging.getLogger(__name__)

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

    # Import here to avoid issues when E2B is not installed
    from e2b_code_interpreter import Sandbox

    start_time = time.time()
    logger.info(f"Creating E2B sandbox (timeout={timeout}s)")

    sbx = None
    try:
        # Create sandbox with API key
        sbx = await asyncio.to_thread(
            Sandbox.create,
            api_key=E2B_API_KEY,
            timeout=timeout
        )
        logger.info(f"Sandbox created in {time.time() - start_time:.2f}s")

        # Upload CSV to sandbox
        await asyncio.to_thread(
            sbx.files.write,
            '/data/input.csv',
            csv_content
        )
        logger.info("CSV uploaded to sandbox")

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
        execution = await asyncio.to_thread(sbx.run_code, code)

        # Process results
        stdout_lines = []
        images = []
        errors = []

        # Collect logs (stdout/stderr)
        if execution.logs:
            if hasattr(execution.logs, 'stdout'):
                for msg in execution.logs.stdout:
                    if hasattr(msg, 'line'):
                        stdout_lines.append(msg.line)
            elif isinstance(execution.logs, list):
                for log in execution.logs:
                    if hasattr(log, 'text'):
                        stdout_lines.append(log.text)
                    elif hasattr(log, 'line'):
                        stdout_lines.append(log.line)

        # Collect results (includes charts/images)
        if execution.results:
            for result in execution.results:
                # Try _repr_png_() method for base64 PNG data
                png_data = None
                if hasattr(result, '_repr_png_'):
                    png_data = result._repr_png_()
                elif hasattr(result, 'png'):
                    png_data = result.png

                if png_data:
                    # Save base64 image to file
                    try:
                        img_data = base64.b64decode(png_data)
                        img_id = str(uuid.uuid4())[:8]
                        OUTPUT_PATH.mkdir(parents=True, exist_ok=True)
                        img_path = OUTPUT_PATH / f"plot_{img_id}.png"
                        img_path.write_bytes(img_data)
                        images.append(str(img_path))
                    except Exception as e:
                        logger.warning(f"Failed to save image: {e}")

                # Collect text output
                if hasattr(result, '__str__'):
                    text = str(result)
                    if text and text != 'None' and not png_data:
                        stdout_lines.append(text)

        # Check for errors
        if execution.error:
            error_msg = str(execution.error)
            if hasattr(execution.error, 'traceback'):
                error_msg = execution.error.traceback
            errors.append(error_msg)

        elapsed = time.time() - start_time
        success = len(errors) == 0
        logger.info(f"E2B execution completed in {elapsed:.2f}s, success={success}")

        return {
            'stdout': '\n'.join(stdout_lines),
            'images': images,
            'errors': errors,
            'success': success
        }

    except Exception as e:
        logger.error(f"E2B unexpected error: {e}")
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
                logger.info("Sandbox terminated")
            except Exception as e:
                logger.warning(f"Failed to kill sandbox: {e}")


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
