"""CSV processing utilities."""

import io
import os
import uuid
import pandas as pd
from pathlib import Path
from fastapi import UploadFile
from .config import UPLOAD_DIR


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
