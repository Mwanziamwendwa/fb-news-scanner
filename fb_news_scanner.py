"""
Kefa's Kenya News Scanner — Full Micro-Agent Edition
Covers: core financial topics, loan-target segments, all national government
ministries, and all 47 Kenyan counties. Each "micro agent" tracks its own
posting history so nothing repeats, and a rotation system spreads posting
across agents fairly over time instead of flooding the Page in one run.

Environment variables required (set as GitHub Secrets):
  PAGE_ACCESS_TOKEN
  PAGE_ID
"""

import os
import json
import time
import requests
import feedparser
from datetime import datetime, timedelta
import pytz

# ---------- GLOBAL CONFIG ----------

PAGE_ID = os.environ["PAGE_ID"]
PAGE_ACCESS_TOKEN = os.environ["PAGE_ACCESS_TOKEN"]
GRAPH_API_VERSION = "v20.0"
GRAPH_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{PAGE_ID}/feed"

NAIROBI_TZ = pytz.timezone("Africa/Nairobi")
POSTED_LOG_FILE = "posted_log.json"
MAX_LOG_SIZE_PER_AGENT = 300
LOOKBACK_HOURS = 24

TOTAL_MAX_POSTS_PER_RUN = 10
AGENTS_PER_RUN = 20


