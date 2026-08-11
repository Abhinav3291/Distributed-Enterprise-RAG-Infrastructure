#Enterprise RAG Infrastructure

A cost-aware, defense-in-depth Retrieval-Augmented Generation (RAG) backend engineered for low-latency responses, high-concurrency throughput, and minimal hallucination risk.

Built as a project to go deep on production-style RAG patterns — rate limiting, caching, retrieval, and output safety — under a real constraint (a small OpenAI API budget), not unlimited cloud spend. Shared here for other students to learn from and for engineers to critique.

---

## 🏗️ System Architecture & Data Flow

```
[HTTP Request]
      │
      ▼
[Redis Token Bucket (Lua Script)] ──(Exceeded)──► [HTTP 429 Too Many Requests]
      │
      ▼
[Input Guardrails]                ──(Malicious)──► [HTTP 400 Bad Request]
      │
      ▼
[Redis SHA-256 Cache Check]       ──(Hit)────────► [Return Cached Payload <10ms]
      │ (Miss)
      ▼
[ChromaDB Vector Search & Cross-Encoder Reranker (`ms-marco-MiniLM-L-6-v2`)]
      │
      ▼
[AsyncOpenAI LLM Inference (Bounded via asyncio.Semaphore)]
      │
      ▼
[Output Groundedness Validation]  ──(Low Overlap)─► [HALLUCINATION_RISK Flag]
      │
      ▼
[Cache Write & JSON Response]
```

Rate limiting runs *first*, before the cache lookup — so throttled or abusive requests never touch Redis, ChromaDB, or the LLM at all.

---

## 📂 Project Directory Structure

```
Enterprise-RAG-Infrastructure/
│
├── Rag_Ingiestion_pipeline/
│   ├── cache/
│   │   ├── __init__.py
│   │   └── redis_cache.py         # SHA-256 query hashing & TTL cache management
│   ├── guardrails/
│   │   ├── __init__.py
│   │   ├── input_guardrails.py    # Prompt injection screening & string sanitization
│   │   └── output_guardrails.py   # Lexical overlap groundedness check
│   ├── middleware/
│   │   ├── __init__.py
│   │   └── rate_limiter.py        # Atomic Token Bucket rate limiter via Redis Lua script
│   ├── data/                      # Local vector storage (ignored in Git)
│   │   └── chroma_db/
│   ├── main.py                    # FastAPI application gateway & routing
│   ├── rag_watchdog.py            # Pipeline orchestration & lifecycle logging
│   └── vector_engine.py           # ChromaDB dense retrieval & cross-encoder reranker
│
├── docker-compose.yml             # Local Redis container deployment configuration
├── requirements.txt               # Pinned project dependencies
├── LICENSE
└── .gitignore                     # Excludes virtual environments, DB artifacts, and secrets
```

---

## ⚖️ Architectural Trade-Offs

Every engineering decision here involves a trade-off. Here's why specific patterns were chosen, and what was intentionally given up.

**1. Inline Async Concurrency (`asyncio.Semaphore`) vs. Distributed Message Queues (Celery / Redis Queue)**
**Choice:** An inline async model throttled by a bounded `asyncio.Semaphore`, instead of a decoupled producer-worker queue.
**Why:** For a single-server deployment handling a moderate concurrency tier (10–20 active requests), a distributed queue adds IPC overhead, operational complexity, and debugging friction that isn't justified yet.
**Trade-off:** Simpler runtime tracing and no queue overhead — but concurrency control is **per-process, not cluster-wide**. Running multiple replicas behind a load balancer would mean each instance throttles independently, blind to the others' load. If the server crashes, in-flight requests are lost with no persistent retry. Moving to a Redis-backed semaphore or a real queue is the next step for coordinated, multi-instance backpressure.

**2. Cross-Encoder Reranking vs. Pure Dense Vector Search**
**Choice:** Candidates from ChromaDB pass through a local `ms-marco-MiniLM-L-6-v2` cross-encoder before reaching the LLM.
**Why:** Bi-encoders embed queries and documents independently, which introduces semantic noise. Cross-encoders score query and document jointly, improving precision.
**Trade-off:** Added CPU/GPU latency per cache-miss query, in exchange for meaningfully better retrieval accuracy.

**3. Redis Token Bucket (Lua Script) vs. Fixed-Window Counters**
**Choice:** Rate limiting via a Token Bucket algorithm, executed atomically through a Redis Lua script.
**Why:** Fixed-window counters allow traffic spikes at window edges. Token buckets replenish smoothly based on elapsed time and avoid race conditions on concurrent requests.
**Trade-off:** More implementation and maintenance complexity than a plain `INCR`-based counter.

**4. Exact-Match Caching vs. Semantic Caching**
**Choice:** Cache keys are a SHA-256 hash of the normalized query + user ID.
**Why:** Simple, fast, and correct with zero false-positive risk.
**Trade-off:** Two users asking the same question with different phrasing won't hit the cache. A semantic cache layer (embedding similarity above a threshold) is planned to catch these near-duplicates.

**5. Lexical-Overlap Groundedness Check vs. a Full Faithfulness Model**
**Choice:** Output is checked for lexical overlap against the retrieved source chunks before being returned.
**Why:** Cheap, fast, and catches a meaningful share of ungrounded responses without adding a second LLM call.
**Trade-off:** This is a heuristic, not a guarantee. A model can hallucinate while still overlapping lexically with the source, or get flagged despite a correct paraphrase. It reduces hallucination risk — it does not eliminate it.

---

## 🚀 Local Installation & Execution Guide

### Prerequisites
- Python 3.10+
- Docker and Docker Compose (for running Redis locally)
- An OpenAI API key

### Step 1: Clone the repository
```bash
git clone https://github.com/Abhinav3291/RAG-Infrastructure.git
cd RAG-Infrastructure
```

### Step 2: Set up environment variables
Create a `.env` file in the project root:
```
OPENAI_API_KEY=your_openai_api_key_here
REDIS_HOST=localhost
REDIS_PORT=6379
```

### Step 3: Spin up the Redis container
```bash
docker-compose up -d
```

### Step 4: Create and activate a virtual environment
```bash
python -m venv venv

# Windows (PowerShell)
.\venv\Scripts\Activate

# macOS/Linux
source venv/bin/activate
```

### Step 5: Install dependencies
```bash
pip install -r requirements.txt
```

### Step 6: Launch the FastAPI server
```bash
uvicorn rag_pipeline.main:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at `http://localhost:8000`.

---

## 🛠️ Tech Stack
Python · FastAPI · AsyncOpenAI · ChromaDB · Redis · HuggingFace Transformers · Docker

---

## 🗺️ Roadmap
- [ ] Semantic (embedding-based) cache layer alongside exact-match hashing
- [ ] Redis-backed semaphore/queue for cross-replica backpressure
- [ ] Per-user daily query quotas
- [ ] Swap local disk for Cloudflare R2 (or similar) for persistent document storage

---

## 📄 License
MIT — see [LICENSE](./LICENSE) for details.#RAG Infrastructure

