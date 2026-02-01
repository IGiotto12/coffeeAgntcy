# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import os
from dotenv import load_dotenv

load_dotenv()  # Automatically loads from `.env` or `.env.local`

DEFAULT_MESSAGE_TRANSPORT = os.getenv("DEFAULT_MESSAGE_TRANSPORT", "NATS")
TRANSPORT_SERVER_ENDPOINT = os.getenv("TRANSPORT_SERVER_ENDPOINT", "nats://localhost:4222")

FARM_BROADCAST_TOPIC = os.getenv("FARM_BROADCAST_TOPIC", "farm_broadcast")

# Scout Agent configuration
SCOUT_PROBE_TIMEOUT_SEC = float(os.getenv("SCOUT_PROBE_TIMEOUT_SEC", "10.0"))  # Increased from 2.0 to 10.0 seconds
SCOUT_INITIAL_TIMEOUT_SEC = float(os.getenv("SCOUT_INITIAL_TIMEOUT_SEC", "2.0"))  # Initial timeout for fast response
SCOUT_RETRY_TIMEOUT_SEC = float(os.getenv("SCOUT_RETRY_TIMEOUT_SEC", "5.0"))  # Retry timeout for better results
SCOUT_MIN_AVAILABLE_FARMS = int(os.getenv("SCOUT_MIN_AVAILABLE_FARMS", "2"))  # Minimum number of available farms for "usable" result
SCOUT_ENABLED = os.getenv("SCOUT_ENABLED", "true").lower() in ("true", "1", "yes")

# Performance Analyzer configuration
PERFORMANCE_ANALYZER_ENABLED = os.getenv("PERFORMANCE_ANALYZER_ENABLED", "true").lower() in ("true", "1", "yes")
PERFORMANCE_CACHE_TTL = int(os.getenv("PERFORMANCE_CACHE_TTL", "300"))  # 5 minutes
DYNAMIC_TIMEOUT_ENABLED = os.getenv("DYNAMIC_TIMEOUT_ENABLED", "true").lower() in ("true", "1", "yes")
CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "localhost")
CLICKHOUSE_PORT = int(os.getenv("CLICKHOUSE_PORT", "9000"))
CLICKHOUSE_USER = os.getenv("CLICKHOUSE_USER", "admin")
CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "admin")
CLICKHOUSE_DATABASE = os.getenv("CLICKHOUSE_DATABASE", "default")

LLM_MODEL = os.getenv("LLM_MODEL", "")
## Oauth2 OpenAI Provider
OAUTH2_CLIENT_ID= os.getenv("OAUTH2_CLIENT_ID", "")
OAUTH2_CLIENT_SECRET= os.getenv("OAUTH2_CLIENT_SECRET", "")
OAUTH2_TOKEN_URL= os.getenv("OAUTH2_TOKEN_URL", "")
OAUTH2_BASE_URL= os.getenv("OAUTH2_BASE_URL", "")
OAUTH2_APPKEY= os.getenv("OAUTH2_APPKEY", "")

LOGGING_LEVEL = os.getenv("LOGGING_LEVEL", "INFO").upper()

ENABLE_HTTP = os.getenv("ENABLE_HTTP", "true").lower() in ("true", "1", "yes")

# This is for demo purposes only. In production, use secure methods to manage API keys.
IDENTITY_API_KEY = os.getenv("IDENTITY_API_KEY", "487>t:7:Ke5N[kZ[dOmDg2]0RQx))6k}bjARRN+afG3806h(4j6j[}]F5O)f[6PD")
IDENTITY_API_SERVER_URL = os.getenv("IDENTITY_API_SERVER_URL", "https://api.agent-identity.outshift.com")
