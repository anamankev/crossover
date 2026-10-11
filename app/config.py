"""Settings read from environment variables.

Locally they come from the .env file. On Render they come from the
dashboard. Secrets are never written in the code.
"""

import os

from dotenv import load_dotenv

load_dotenv()

QLOO_API_URL = os.getenv("QLOO_API_URL", "https://hackathon.api.qloo.com").rstrip("/")
QLOO_API_KEY = os.getenv("QLOO_API_KEY", "")

LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")

# Render sets this to the app's public address. Used by the keepalive ping.
PUBLIC_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")

# Shown as a link in the page footer.
REPO_URL = os.getenv("REPO_URL", "")

# How many recommendations each side returns.
TAKE = 8
