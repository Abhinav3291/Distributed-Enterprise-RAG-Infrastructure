RAG Engine
An enterprise-grade, low-latency Retrieval-Augmented Generation backend engineered for deterministic precision, high-concurrency throughput, and zero-trust runtime safety.

🏗️ System Architecture & Data Flow
Plaintext
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
[Output Groundedness Validation]  ──(Failed)─────► [HALLUCINATION_RISK Block]
      │
      ▼
[Cache Write & JSON Response]
📂 Project Directory Structure
Plaintext
Distributed Enterprise RAG Infrastructure/
│
├── Rag_Ingiestion_pipeline.py/
│   ├── cache/
│   │   ├── __init__.py
│   │   └── redis_cache.py         # SHA-256 query hashing & TTL cache management
│   ├── guardrails/
│   │   ├── __init__.py
│   │   ├── input_guardrails.py    # Prompt injection screening & string sanitization
│   │   └── output_guardrails.py   # Lexical overlap hallucination evaluation
│   ├── middleware/
│   │   ├── __init__.py
│   │   └── rate_limitter.py       # Atomic Token Bucket rate limiter via Redis Lua script
│   ├── data/                      # Local vector storage (Ignored in Git)
│   │   └── chroma_db/
│   ├── main.py                    # FastAPI application gateway & routing
│   ├── rag_watchdog.py            # Pipeline orchestration & lifecycle logging
│   └── vector_engine.py           # ChromaDB dense retrieval & Cross-Encoder reranker
│
├── docker-compose.yml             # Local Redis container deployment configuration
├── requirements.txt               # Pinned project dependencies
└── .gitignore                     # Excludes virtual environments, DB artifacts, and secrets
⚖️ Architectural Trade-Offs
Every engineering decision involves trade-offs. Here is a breakdown of why specific architectural patterns were chosen for Bastion RAG Engine, along with what was intentionally compromised:

1. Inline Async Concurrency (asyncio.Semaphore) vs. Distributed Message Queues (Celery / Redis Queue)
The Choice: We abandoned a decoupled Producer-Worker message queue architecture in favor of an Inline Async Model throttled by a memory-bound asyncio.Semaphore.

Why: For single-server deployments handling high concurrency tiers (10–20 active requests), a distributed queue adds severe Inter-Process Communication (IPC) overhead, code complexity, and debugging friction.

The Trade-Off: While it eliminates queue overhead and simplifies runtime tracing, it means the application is limited to single-node scaling. If the server instance crashes, active in-flight requests running in the semaphore are lost (no persistent task retry out-of-the-box).

2. Cross-Encoder Reranking vs. Pure Dense Vector Search
The Choice: Retrieved candidate document chunks from ChromaDB are passed through a local HuggingFace ms-marco-MiniLM-L-6-v2 Cross-Encoder before hitting the LLM.

Why: Standard bi-encoders embed queries and documents independently, frequently introducing semantic noise and positional bias. Cross-encoders process them jointly using deep cross-attention, drastically boosting precision.

The Trade-Off: Increased CPU/GPU compute time per query miss. While vector search alone takes milliseconds, cross-encoder scoring adds latency overhead to ensure top-tier answer accuracy.

3. Redis Token Bucket via Lua Script vs. Fixed-Window Counters
The Choice: Rate limiting is enforced using a Token Bucket algorithm executed via an atomic Redis Lua script.

Why: Fixed-window counters allow traffic spikes at window edges. Token buckets allow smooth burst handling while dynamically replenishing tokens based on exact elapsed time, executed atomically to prevent race conditions.

The Trade-Off: Slightly higher code complexity and maintenance overhead compared to standard Redis INCR commands.

🚀 Local Installation & Execution Guide
Prerequisites
Python 3.10+

Docker and Docker Compose (for running Redis locally)

Step 1: Clone the Repository
Bash
git clone https://github.com/Abhinav3291/Distributed-Enterprise-RAG-Infrastructure.git
cd "Distributed Enterprise RAG Infrastructure & Execution Engine"
Step 2: Set Up Environment Variables
Create a .env file in the root project directory:

Code snippet
OPENAI_API_KEY=your_openai_api_key_here
REDIS_HOST=localhost
REDIS_PORT=6379
Step 3: Spin Up Redis Container
Launch the Redis service via Docker Compose:

Bash
docker-compose up -d
Step 4: Create and Activate a Python Virtual Environment
Bash
python -m venv venv
# On Windows (PowerShell):
.\venv\Scripts\Activate
# On macOS/Linux:
source venv/bin/activate
Step 5: Install Dependencies
Bash
pip install -r requirements.txt
Step 6: Launch the FastAPI Server
Run the application using Uvicorn with auto-reload enabled:

Bash
uvicorn Rag_Ingiestion_pipeline.py.main:app --reload --host 0.0.0.0 --port 8000
