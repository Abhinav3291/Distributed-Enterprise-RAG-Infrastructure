Bastion RAG Engine
An enterprise-grade, low-latency Retrieval-Augmented Generation backend engineered for deterministic precision, high-concurrency throughput, and zero-trust runtime safety.

🏗️ System Architecture & Data Flow
Plaintext
[HTTP Request] 
      │
      ▼
[Redis Rate Limiter] ──(Exceeded)──► [HTTP 429 Too Many Requests]
      │
      ▼
[Input Guardrails]   ──(Malicious)──► [HTTP 400 Bad Request]
      │
      ▼
[Redis Cache Check]  ──(Hit)────────► [Return Cached Payload <10ms]
      │ (Miss)
      ▼
[ChromaDB Vector Search & Cross-Encoder Reranker (`ms-marco-MiniLM-L-6-v2`)]
      │
      ▼
[AsyncOpenAI LLM Inference]
      │
      ▼
[Output Groundedness Validation] ──(Failed)──► [HALLUCINATION_RISK Block]
      │
      ▼
[Cache Write & JSON Response]
⚙️ Core Engineering Highlights
Asynchronous Concurrency: Non-blocking FastAPI event loop governed by bounded asyncio.Semaphore throttling, eliminating distributed queue IPC overhead while ensuring robust single-node backpressure protection.

Redis Dual-State Engine:

Caching: Cryptographic SHA-256 query hashing for sub-millisecond payload retrieval and Thundering Herd mitigation.

Security: Atomic sliding-window rate-limiter (INCR / EXPIRE) to prevent API abuse and token exhaustion.

Hybrid Precision Retrieval: ChromaDB dense vector search augmented with a local Cross-Encoder (ms-marco-MiniLM-L-6-v2) reranker to eradicate semantic noise and maximize contextual relevance.

Defensive Guardrails: Dual-boundary validation pipeline enforcing adversarial input sanitization against prompt injection and strict lexical groundedness verification to intercept hallucination vectors.

🛠️ Tech Stack
Core Framework: Python, FastAPI, AsyncOpenAI

Retrieval & Reranking: ChromaDB, HuggingFace Transformers (ms-marco-MiniLM-L-6-v2)

Infrastructure & State: Redis (Caching & Rate Limiting)

Document Processing: PyMuPDF, Custom Security Middleware

🚀 Getting Started
1. Clone the Repository
Bash
git clone https://github.com/Abhinav3291/Distributed-Enterprise-RAG-Infrastructure.git
cd "Distributed Enterprise RAG Infrastructure & Execution Engine"
2. Configure Environment Variables
Create a .env file in the root directory and add your API credentials:

Code snippet
OPENAI_API_KEY=your_openai_api_key_here
REDIS_HOST=localhost
REDIS_PORT=6379
3. Spin Up Redis via Docker Compose
Bash
docker-compose up -d
4. Install Dependencies
Bash
pip install -r requirements.txt
5. Launch the Application
Bash
uvicorn Rag_Ingiestion_pipeline.py.main:app --reload --host 0.0.0.0 --port 8000
