import os

# ── Bootstrap ──────────────────────────────────────────────────────────────
BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:29092")

# ── Topic 名稱 ─────────────────────────────────────────────────────────────
TOPIC_TICKER_TASKS    = "ticker-tasks"
TOPIC_DLQ             = "ticker-tasks.DLQ"
TOPIC_ANALYSIS_RESULTS = "analysis-results"
TOPIC_AGENT_THOUGHTS  = "agent-thoughts"
TOPIC_FINAL_REPORTS   = "final-reports"

# ── Consumer Group ─────────────────────────────────────────────────────────
GROUP_WORKERS     = "workers"
GROUP_AGGREGATORS = "aggregators"
