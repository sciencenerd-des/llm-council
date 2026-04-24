"""Configuration for the LLM Council."""

import os
from dotenv import load_dotenv

load_dotenv()

# OpenRouter API key
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Supabase configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Council members - list of OpenRouter model identifiers
COUNCIL_MODELS = [
    "google/gemini-3-flash-preview",
    "deepseek/deepseek-v3.2-speciale",
    "z-ai/glm-4.7",
    "minimax/minimax-m2.1",
]

# Chairman model - synthesizes final response
CHAIRMAN_MODEL = "moonshotai/kimi-k2-thinking"

# Vision-capable chairman for analyzing dashboard images
# Used when models generate visualizations during CSV analysis
# Gemini 3 Flash Preview (Nano Banana Pro) - frontier multimodal model
VISION_CHAIRMAN_MODEL = "google/gemini-3-flash-preview"

# OpenRouter API endpoint
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Data directory for conversation storage
DATA_DIR = "data/conversations"

# Upload directory for full CSV files
UPLOAD_DIR = "data/uploads"

# Output directory for generated plots
OUTPUT_DIR = "data/outputs"

# E2B configuration for cloud sandbox code execution
E2B_API_KEY = os.getenv("E2B_API_KEY")
E2B_SANDBOX_TIMEOUT = 60  # seconds per sandbox
E2B_ENABLED = os.getenv("E2B_ENABLED", "true").lower() == "true"
