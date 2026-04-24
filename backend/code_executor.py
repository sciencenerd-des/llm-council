"""Jupyter kernel-based code execution for LLM Council."""

import asyncio
import base64
import concurrent.futures
import re
import uuid
from pathlib import Path
from typing import Dict, Any, Tuple
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


def validate_code(code: str) -> Tuple[bool, str]:
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


def _run_code_sync(code: str, csv_path: str, timeout: int = 30) -> Dict[str, Any]:
    """
    Run code synchronously in a Jupyter kernel.
    This runs in a separate thread to avoid ZMQ/asyncio conflicts.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    outputs = []
    images = []
    errors = []

    km = None
    kc = None

    try:
        # Start kernel
        km = KernelManager(kernel_name='python3')
        km.start_kernel()
        kc = km.client()
        kc.start_channels()
        kc.wait_for_ready(timeout=10)

        # Initialize with common imports
        # Use %matplotlib inline to properly capture figures as display_data
        init_code = """
%matplotlib inline
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import warnings
warnings.filterwarnings('ignore')
plt.style.use('seaborn-v0_8-whitegrid')
"""
        kc.execute(init_code)
        # Wait for init to complete
        while True:
            msg = kc.get_iopub_msg(timeout=10)
            if msg['msg_type'] == 'status' and msg['content']['execution_state'] == 'idle':
                break

        # Load CSV
        load_code = f"df = pd.read_csv('{csv_path}')"
        kc.execute(load_code)
        while True:
            msg = kc.get_iopub_msg(timeout=10)
            if msg['msg_type'] == 'status' and msg['content']['execution_state'] == 'idle':
                break

        # Execute user code
        kc.execute(code)

        # Collect output
        import time
        deadline = time.time() + timeout

        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                errors.append("Execution timed out after 30 seconds")
                break

            try:
                msg = kc.get_iopub_msg(timeout=min(remaining, 1.0))
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
            except Exception:
                # Timeout on get_iopub_msg, continue
                continue

    except Exception as e:
        errors.append(str(e))
    finally:
        # Clean shutdown
        try:
            if kc:
                kc.stop_channels()
            if km:
                km.shutdown_kernel(now=True)
        except Exception:
            pass

    return {
        'stdout': '\n'.join(outputs),
        'images': images,
        'errors': errors,
        'success': len(errors) == 0
    }


async def execute_code_for_model(code: str, csv_path: str) -> Dict[str, Any]:
    """
    Execute code for a model asynchronously.
    Runs the synchronous kernel code in a thread pool to avoid blocking.
    """
    # Validate code first
    is_valid, error_msg = validate_code(code)
    if not is_valid:
        return {
            'stdout': '',
            'images': [],
            'errors': [error_msg],
            'success': False
        }

    # Run synchronous code in thread pool
    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor() as pool:
        result = await loop.run_in_executor(
            pool,
            _run_code_sync,
            code,
            csv_path
        )

    return result
