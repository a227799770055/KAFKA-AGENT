# Stock Analysis Multi-Agent System — 完整開發規劃書
> 本文件供 Claude Code 閱讀，作為開發的唯一依據。請嚴格按照本規劃書的架構、命名規範與實作細節進行開發。

---

## 1. 專案概述

### 1.1 目標
構建一個基於 Kafka 的分散式 Multi-Agent 系統，能夠接收複雜的股票比較查詢（例如：「幫我分析 NVDA 和 TSLA 的近況」），透過三個專職 Agent 的協作，產出一份 Markdown 格式的專業股票對比報告。

### 1.2 核心展示能力
- Kafka Producer / Consumer 的正確使用
- Multi-Agent 協作與任務拆解
- ReAct（Reason + Act）循環與 Tool Use
- 錯誤容忍：DLQ（Dead Letter Queue）+ 指數退避 Retry
- Redis 原子鎖防止重複合成
- 完整的 Docker Compose 容器化

### 1.3 技術堆疊
| 類別 | 技術 |
|------|------|
| 訊息佇列 | Apache Kafka (via `confluent-kafka`) |
| 快取/狀態 | Redis 7 |
| AI 模型 | Google Gemini（Mock 模式可切換） |
| 金融數據 | `yfinance`、`finnhub-python`（可降級 mock） |
| 資料驗證 | `pydantic` v2 |
| 容器化 | Docker + Docker Compose |
| 測試 | `pytest` + `pytest-asyncio` |
| 語言 | Python 3.11 |

---

## 2. 系統架構

### 2.1 資料流全景

```
使用者輸入 (CLI)
      │
      ▼
┌─────────────────┐
│   Decomposer    │  Producer
│  (gemini-flash) │  → 解析語意，拆解出多個 Ticker
└────────┬────────┘
         │ 發布至 topic: ticker-tasks
         │ 同時在 Redis 登記 task metadata
         ▼
┌─────────────────┐
│  ticker-tasks   │  Kafka Topic (3 partitions, key=ticker)
└────────┬────────┘
         │ 多個 Worker 並行消費
    ┌────┴────┐
    ▼         ▼
┌────────┐ ┌────────┐   Consumer Group: workers
│Worker 1│ │Worker 2│   → ReAct 循環 + Tool Use
└───┬────┘ └───┬────┘   → 呼叫 yfinance / mock API
    │           │
    └─────┬─────┘
          │ 發布至 topic: analysis-results
          │ 失敗則發布至 topic: ticker-tasks.DLQ
          ▼
┌─────────────────────┐
│     Aggregator      │  Consumer
│   (gemini-pro)      │  → 收齊所有結果後合成最終報告
│  + Redis 原子鎖      │  → 防止重複觸發
└──────────┬──────────┘
           │ 發布至 topic: final-reports
           ▼
    最終報告輸出 (stdout / 檔案)

── 所有 Agent 同時發布思考碎片至 ──▶  topic: agent-thoughts
```

### 2.2 Kafka Topic 規格

| Topic | Partitions | Retention | Key | 用途 |
|-------|-----------|-----------|-----|------|
| `ticker-tasks` | 3 | 1h | ticker | Worker 任務佇列 |
| `ticker-tasks.DLQ` | 1 | 24h | ticker | 失敗任務存放 |
| `analysis-results` | 1 | 1h | task_id | Worker 輸出結果 |
| `agent-thoughts` | 1 | 1h | agent_role | ReAct 思考碎片 |
| `final-reports` | 1 | 24h | task_id | 最終對比報告 |

### 2.3 Redis Key 規格

| Key Pattern | Type | TTL | 用途 |
|-------------|------|-----|------|
| `task:{task_id}:meta` | Hash | 10min | total, created_at, query |
| `task:{task_id}:completed` | Integer | 10min | 已完成的 subtask 數量 |
| `task:{task_id}:results` | List | 10min | 各 Ticker 分析結果暫存 |
| `task:{task_id}:synthesis_lock` | String | 60s | 防重複合成的原子鎖 |

---

## 3. Agent 定義

### 3.1 Decomposer（Producer）

**職責：** 接收使用者自然語言輸入，解析出股票代碼清單，為每個 Ticker 建立獨立任務並發布到 Kafka。

**模型：** `gemini-1.5-flash`（Mock 模式下直接使用 regex 解析）

**輸入：** CLI 字串，例如 `"分析 NVDA 和 TSLA 的近況並做比較"`

**輸出（發布到 `ticker-tasks`）：**
```json
{
  "schema_version": "1.0",
  "task_id": "uuid4",
  "correlation_id": "uuid4",
  "ticker": "NVDA",
  "total_subtasks": 2,
  "created_at": "2025-01-01T00:00:00Z"
}
```

**Redis 操作：**
- `HSET task:{task_id}:meta total 2 query "原始問題" created_at "..."`
- `SET task:{task_id}:completed 0`
- 設定 TTL 600 秒

---

### 3.2 Worker（Consumer）

**職責：** 從 `ticker-tasks` 領取單一 Ticker 任務，執行 ReAct 循環，產出該股票的研究摘要。

**模型：** `gemini-1.5-flash`（Mock 模式下回傳固定格式的假資料）

**Consumer Group：** `workers`（可水平擴展）

**ReAct 循環規格（最多 3 輪）：**

```
Iteration 1:
  THOUGHT → 決定先取得股價
  ACTION  → 呼叫 get_stock_price(ticker)
  OBS     → 獲得價格數據

Iteration 2:
  THOUGHT → 決定取得近期新聞
  ACTION  → 呼叫 get_company_news(ticker)
  OBS     → 獲得新聞摘要

Iteration 3:
  THOUGHT → 資料足夠，開始撰寫摘要
  ACTION  → 呼叫 generate_summary(ticker, price_data, news_data)
  OBS     → 最終摘要文字
```

**每個 THOUGHT 步驟都必須發布到 `agent-thoughts`：**
```json
{
  "task_id": "uuid",
  "correlation_id": "uuid",
  "agent_role": "worker",
  "ticker": "NVDA",
  "step": "THOUGHT | ACTION | OBSERVATION",
  "iteration": 1,
  "content": "正在分析 NVDA 的股價走勢...",
  "timestamp": "2025-01-01T00:00:00Z"
}
```

**成功輸出（發布到 `analysis-results`）：**
```json
{
  "schema_version": "1.0",
  "task_id": "uuid",
  "correlation_id": "uuid",
  "ticker": "NVDA",
  "content": "## NVDA 分析報告\n...",
  "iterations_used": 3,
  "timestamp": "2025-01-01T00:00:00Z"
}
```

**失敗處理（DLQ）：**
- Retry 最多 3 次，使用指數退避：1s → 2s → 4s
- 耗盡後發布到 `ticker-tasks.DLQ`，附帶 `error_type` 與 `stack_trace`
- 發布 DLQ 後，更新 Redis `task:{task_id}:completed` +1（標記為「已處理，但失敗」），避免 Aggregator 永遠等待

**Tool 規格（`src/worker/tools.py`）：**

```python
def get_stock_price(ticker: str) -> dict:
    """
    回傳格式：
    {
      "ticker": "NVDA",
      "current_price": 875.4,
      "change_pct": 2.3,
      "volume": 45000000,
      "52w_high": 974.0,
      "52w_low": 402.0
    }
    Mock 模式：回傳隨機但合理的假數據。
    Real 模式：使用 yfinance.Ticker(ticker).info
    """

def get_company_news(ticker: str, days: int = 7) -> list[dict]:
    """
    回傳格式：
    [{"title": "...", "summary": "...", "sentiment": "positive|neutral|negative"}]
    Mock 模式：回傳 2 筆固定假新聞。
    Real 模式：使用 finnhub client.company_news()
    """

def generate_summary(ticker: str, price_data: dict, news_data: list) -> str:
    """
    呼叫 LLM（或 Mock）產生 Markdown 格式的單股分析摘要。
    輸出為純 Markdown 字串。
    """
```

---

### 3.3 Aggregator（Consumer）

**職責：** 監聽 `analysis-results`，追蹤任務完成進度，當所有 Ticker 都完成後，使用高階模型產出橫向對比報告。

**模型：** `gemini-1.5-pro`（Mock 模式下使用模板字串組合）

**Consumer Group：** `aggregators`

**聚合邏輯：**
1. 收到一筆 `analysis-results`
2. 將結果 `RPUSH` 到 `task:{task_id}:results`
3. `INCR task:{task_id}:completed`
4. 讀取 `task:{task_id}:meta` 中的 `total`
5. 若 `completed >= total`：嘗試取得 Redis 原子鎖
6. 取鎖成功：觸發 Final Synthesis，發布到 `final-reports`
7. 取鎖失敗：另一個 Aggregator 已處理，直接跳過

**Redis 原子鎖實作（必須使用 Lua Script）：**
```lua
-- 原子性：只有第一個到達的 Aggregator 能取得鎖
if redis.call('SET', KEYS[1], '1', 'NX', 'EX', '60') then
  return 1
else
  return 0
end
```

**最終報告輸出（發布到 `final-reports`，同時寫入本地檔案）：**
```json
{
  "schema_version": "1.0",
  "task_id": "uuid",
  "original_query": "分析 NVDA 和 TSLA 的近況",
  "tickers": ["NVDA", "TSLA"],
  "report": "## 股票對比分析報告\n...",
  "generated_at": "2025-01-01T00:00:00Z"
}
```

報告同時儲存到 `./reports/{task_id}.md`。

---

## 4. 目錄結構（完整版）

```
.
├── docker-compose.yml
├── .env.example                    # API Keys 範本，不得 commit 真實 key
├── .env                            # 實際使用（gitignore）
├── configs/
│   ├── kafka_config.py             # Kafka broker 設定、topic 名稱常數
│   └── prompts.py                  # 所有 LLM Prompt Templates
├── scripts/
│   └── init_topics.py              # 自動建立 Kafka Topics 的初始化腳本
├── src/
│   ├── common/
│   │   ├── __init__.py
│   │   ├── kafka_wrapper.py        # KafkaProducer / KafkaConsumer 封裝
│   │   ├── redis_client.py         # Redis 連線、Lua Script 原子鎖
│   │   ├── schemas.py              # 所有 Pydantic Message Models
│   │   ├── retry.py                # 指數退避 Retry 裝飾器
│   │   └── logging_config.py       # 結構化 logging 設定（JSON 格式）
│   ├── decomposer/
│   │   ├── __init__.py
│   │   ├── main.py                 # CLI 入口點
│   │   └── agent.py                # Decomposer Agent 邏輯
│   ├── worker/
│   │   ├── __init__.py
│   │   ├── main.py                 # Consumer 啟動入口
│   │   ├── agent.py                # ReAct 循環主邏輯
│   │   └── tools.py                # Tool Use 實作（含 Mock 模式）
│   └── aggregator/
│       ├── __init__.py
│       ├── main.py                 # Consumer 啟動入口
│       └── agent.py                # 聚合邏輯 + Redis 原子鎖
├── tests/
│   ├── unit/
│   │   ├── test_schemas.py         # Pydantic Schema 驗證測試
│   │   ├── test_retry.py           # 指數退避邏輯測試
│   │   ├── test_tools.py           # Tool Use Mock 測試
│   │   └── test_decomposer.py      # Decomposer 解析邏輯測試
│   └── integration/
│       └── test_pipeline.py        # 端對端流程測試（需 Docker）
├── reports/                        # 最終報告輸出目錄（gitignore 內容）
└── README.md
```

---

## 5. docker-compose.yml 規格

包含以下服務，**順序與 depends_on 必須正確**：

| 服務名稱 | Image | 用途 |
|---------|-------|------|
| `zookeeper` | `confluentinc/cp-zookeeper:7.5.0` | Kafka 協調者 |
| `kafka` | `confluentinc/cp-kafka:7.5.0` | 訊息 Broker |
| `kafka-ui` | `provectuslabs/kafka-ui:latest` | 管理介面，port 8080 |
| `redis` | `redis:7-alpine` | 狀態儲存 |
| `decomposer` | build: `./src/decomposer` | 任務拆解 Agent |
| `worker` | build: `./src/worker` | 研究 Agent（`replicas: 2`） |
| `aggregator` | build: `./src/aggregator` | 聚合 Agent |

**重要設定：**
- Kafka 需設定 `KAFKA_AUTO_CREATE_TOPICS_ENABLE: 'false'`（由 `scripts/init_topics.py` 管理）
- 所有服務透過 `.env` 取得 `GEMINI_API_KEY`、`REDIS_URL`、`KAFKA_BOOTSTRAP_SERVERS`
- Worker 服務需設定 `deploy.replicas: 2` 展示水平擴展

---

## 6. 環境變數規格（.env.example）

```bash
# LLM
GEMINI_API_KEY=your_key_here
USE_MOCK_LLM=true              # true = Mock 模式，false = 真實 Gemini API

# Kafka
KAFKA_BOOTSTRAP_SERVERS=kafka:9092

# Redis
REDIS_URL=redis://redis:6379/0

# Financial Data
USE_MOCK_FINANCE=true          # true = Mock 工具，false = 真實 yfinance
FINNHUB_API_KEY=your_key_here

# System
LOG_LEVEL=INFO
TASK_TIMEOUT_SECONDS=300       # Aggregator 等待超時時間
MAX_WORKER_ITERATIONS=3        # ReAct 最大循環次數
WORKER_RETRY_MAX=3             # DLQ Retry 最大次數
```

---

## 7. Pydantic Schema 規格（src/common/schemas.py）

以下是所有需要實作的 Message Model：

```python
class TickerTask(BaseModel):
    schema_version: str = "1.0"
    task_id: str                    # UUID4
    correlation_id: str             # UUID4，追蹤整個請求鏈
    ticker: str                     # 大寫股票代碼，e.g. "NVDA"
    total_subtasks: int
    created_at: datetime

class AnalysisResult(BaseModel):
    schema_version: str = "1.0"
    task_id: str
    correlation_id: str
    ticker: str
    content: str                    # Markdown 格式報告
    iterations_used: int
    timestamp: datetime

class AgentThought(BaseModel):
    task_id: str
    correlation_id: str
    agent_role: Literal["decomposer", "worker", "aggregator"]
    ticker: Optional[str]
    step: Literal["THOUGHT", "ACTION", "OBSERVATION"]
    iteration: int
    content: str
    timestamp: datetime

class FinalReport(BaseModel):
    schema_version: str = "1.0"
    task_id: str
    original_query: str
    tickers: list[str]
    report: str                     # Markdown 格式最終對比報告
    generated_at: datetime

class DLQMessage(BaseModel):
    original_message: TickerTask
    error_type: str
    stack_trace: str
    retry_count: int
    failed_at: datetime
```

---

## 8. 單元測試規格（tests/unit/）

### test_retry.py
- 測試重試 3 次後仍失敗會拋出例外
- 測試延遲時間符合指數退避（1s, 2s, 4s ± 誤差）
- 測試成功後不繼續重試

### test_schemas.py
- 測試合法 TickerTask 能通過驗證
- 測試缺少必填欄位會拋出 ValidationError
- 測試 ticker 欄位自動轉大寫（若有實作 validator）

### test_tools.py
- Mock 模式下 `get_stock_price` 回傳正確格式
- Mock 模式下 `get_company_news` 回傳至少 1 筆新聞
- 回傳值符合 Pydantic Schema

### test_decomposer.py
- 輸入「分析 NVDA 和 TSLA」能正確解析出 `["NVDA", "TSLA"]`
- 輸入單一股票能正確解析出 `["AAPL"]`
- 輸入無效字串能優雅回傳空清單或拋出已定義例外

---

## 9. 開發順序指引（給 Claude Code）

請**嚴格按照以下順序**開發，每個步驟完成後確認可執行再進行下一步：

```
Step 1: 建立專案骨架
  → 建立所有目錄與空的 __init__.py
  → 建立 .env.example

Step 2: docker-compose.yml
  → 包含 zookeeper, kafka, kafka-ui, redis
  → 確認 docker-compose up 能成功啟動基礎設施

Step 3: scripts/init_topics.py
  → 自動建立所有 Kafka Topics（含正確 partition 數）

Step 4: src/common/
  → schemas.py（Pydantic Models）
  → kafka_wrapper.py（Producer/Consumer 封裝）
  → redis_client.py（含 Lua Script）
  → retry.py（指數退避）
  → logging_config.py

Step 5: src/worker/tools.py
  → Mock 模式全部實作完成並可單獨測試

Step 6: src/worker/agent.py + main.py
  → ReAct 循環可獨立運行

Step 7: src/decomposer/agent.py + main.py
  → 可解析輸入並正確發布到 Kafka

Step 8: src/aggregator/agent.py + main.py
  → 聚合邏輯 + Redis 原子鎖

Step 9: 加入 Agent Dockerfiles
  → 每個 Agent 有獨立 Dockerfile
  → docker-compose.yml 加入三個 Agent 服務

Step 10: tests/unit/
  → 四個測試檔案全部通過

Step 11: 端對端驗證
  → docker-compose up
  → 執行 decomposer CLI：python -m src.decomposer.main "分析 NVDA 和 TSLA"
  → 觀察 Kafka-UI 中訊息流動
  → 確認 reports/ 目錄產出最終報告
```

---

## 10. 驗收標準

專案完成後，必須滿足以下條件：

- [ ] `docker-compose up` 能一鍵啟動所有服務，無報錯
- [ ] 執行 `python -m src.decomposer.main "分析 NVDA 和 TSLA"` 後，`reports/` 目錄內出現最終報告
- [ ] Kafka-UI（localhost:8080）能觀察到所有 Topic 的訊息流動
- [ ] `pytest tests/unit/` 全部通過
- [ ] Worker 失敗時，`ticker-tasks.DLQ` Topic 有對應訊息
- [ ] 所有 Agent 的思考碎片可在 `agent-thoughts` Topic 中觀察到
- [ ] Mock 模式（`USE_MOCK_LLM=true`）下不需要真實 API Key 也能完整運行

---

## 11. 已知限制與後續改善方向（供面試討論）

1. **Schema Registry 缺失**：目前用 Pydantic 做本地驗證，生產環境應搭配 Confluent Schema Registry 做集中管理。
2. **Aggregator 單點**：雖有 Redis 鎖，但 Aggregator 目前設計為單一容器，可進一步設計 leader election。
3. **Timeout 處理**：Task TTL 到期後，Aggregator 目前不會主動觸發部分結果報告，可作為後續改善項目。
4. **Observability**：缺少 Prometheus metrics 與 Grafana dashboard，生產環境應補充。
5. **LLM 成本控制**：ReAct 最大 3 輪限制是硬編碼，理想上應根據任務複雜度動態調整。
