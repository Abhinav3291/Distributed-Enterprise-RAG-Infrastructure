1. Asynchronous Ingestion Pipeline: Race Condition & Concurrency Mitigation
	Problem Statement
When accepting heavy PDF uploads from 100+ simultaneous users in a naive or synchronous setup, traditional folder-watching (e.g., Watchdog) and inline file handling fail due to three critical bottlenecks:
1.	File Corruption & Race Conditions: Reading incomplete (0-byte or partially written) files while OS/network streams are actively writing, throwing EmptyFileError or EOFError exceptions.
2.	Server Memory Exhaustion (OOM Crashes): Parsing and embedding multiple multi-page PDFs concurrently spikes system RAM (100–300 MB per file), triggering Out-Of-Memory (OOMKilled) process crashes.
3.	Thread Blocking & Gateway Timeouts: Synchronous PDF parsing blocks HTTP request threads, causing high user latency and 504 Gateway Timeouts.

 <img width="843" height="460" alt="image" src="https://github.com/user-attachments/assets/e64451ae-d803-4e10-8f61-340e92ecea69" />
Fig 1: Synchronous Watchdog File-System Ingestion Architecture 



	Key Architectural Mitigations
	1. Zero-Copy Ingress & Presigned URLs (Prevents File Corruption)
•	Mechanism: Clients bypass the application server entirely by requesting an S3/MinIO Presigned URL and uploading directly to cloud/object storage.

•	Mitigation: Object storage only emits an ObjectCreated event webhook after the byte stream is 100% written and verified. This eliminates local filesystem lock race conditions and prevents workers from attempting to parse half-written files.

<img width="731" height="399" alt="image" src="https://github.com/user-attachments/assets/b577d8de-7522-467f-b9f4-d2a722d11f85" />
 
Fig 2: Production Event-Driven Asynchronous Ingestion Pipeline
	2. Asynchronous Job Delegation (Eliminates Thread Blocking)
•	Mechanism: Upon receiving an upload request, the API Gateway immediately issues a 202 Accepted response with a unique JobID and pushes a lightweight JSON payload ({job_id, s3_key, user_id}) into the message queue.
•	Mitigation: The client connection is closed in under 50ms. Clients track processing progress asynchronously via polling (GET /api/v1/jobs/{job_id}), WebSockets, or Server-Sent Events (SSE).


	3. Queue-Driven Backpressure & Bounded Worker Pools (Prevents OOM Crashes)
•	Mechanism: Ingestion workers consume jobs from a durable message broker (RabbitMQ / Redis Streams) configured with strict prefetch limits (basic_qos(prefetch_count=1)).

•	Mitigation: If 100+ users upload PDFs at the exact same millisecond, the files buffer safely in the message queue. Worker nodes pull tasks at a fixed execution rate matching system hardware capacity, maintaining a flat memory footprint and preventing OOM crashes.


				PDF Ingestion Request
					│
	 ┌─────────────────────    ┴─────────────────────┐
 ▼                                                                                                   ▼
[ Phase 1: Local Development ]              [ Phase 2: Production Cloud ]
• Local Folder Listener (Watchdog)          • Direct Ingress via S3 Presigned URLs
• In-Memory ThreadPoolExecutor              • Asynchronous RabbitMQ Broker
• In-Process File Read-Lock Checks          • Bounded Distributed Worker Containers
• Chroma DB Thread-Locked Writes            • Distributed Vector DB Cluster





Topic: Transition from Distributed Message Queue to Inline Async Concurrency
Target Concurrency: 10–20 Simultaneous Requests
	1. Executive Summary
This document details the architectural refactoring of the Retrieval-Augmented Generation (RAG) backend pipeline. The system was previously designed using an asynchronous Producer-Worker Pattern backed by Redis queue structures.
To optimize for a single-server deployment handling 10–20 concurrent requests, the background message queue layer (producer.py and worker.py) was removed. It has been replaced with an Inline Async Concurrency Model using asyncio.Semaphore. This transition significantly reduces system complexity, removes process-level IPC overhead, simplifies debugging, and maintains low latency while protecting downstream Vector DB and LLM APIs from overload.
	2. Architecture BEFORE Refactoring (Producer-Worker Model)
		System Description
		In this pattern, request ingestion was decoupled from execution.
1.	Producer (producer.py): Receives HTTP requests, validates safety guidelines, checks rate limits, and pushes query tasks into a Redis Queue (rag_query_queue).
2.	Message Queue: Redis list/stream holding serialized JSON task payloads.
3.	Worker Process (worker.py): A standalone background daemon continuously polling the queue via blocking read (BRPOP). Before performing vector searches or calling the LLM, the worker re-checks the Redis cache to prevent duplicate computation (Thundering Herd protection).

					Systematic Diagram: BEFORE Refactoring

 <img width="788" height="430" alt="image" src="https://github.com/user-attachments/assets/8d7a1c62-78ab-4ac2-a369-88f7727f278c" />


	


Architecture AFTER Refactoring (Inline Async Model)
	System Description
	In the streamlined pattern, FastAPI handles incoming requests directly in a single event loop.
1.	Synchronous Execution Flow: Requests run inline within the FastAPI route handler.
2.	Non-Blocking I/O: All network calls (Redis cache, Vector DB search, and LLM API inference) use non-blocking async/await calls (httpx or AsyncOpenAI).
3.	In-Memory Concurrency Throttle (asyncio.Semaphore): Replaces the external message queue. A semaphore capped at 10 slots ensures that if 20 requests arrive simultaneously, 10 execute immediately while the remaining 10 pause cleanly in memory before processing.

<img width="743" height="406" alt="image" src="https://github.com/user-attachments/assets/c3cca94d-6a12-416a-99a0-8b749450acb3" />

 


•	Why make transition from : A distributed message queue with separate worker processes (using Redis Streams, Celery, or custom polling) is designed for thousands of long-running asynchronous jobs distributed across multiple physical server nodes. For 10 to 20 concurrent requests, running background worker daemons introduces heavy infrastructure friction without providing any actual throughput benefits.
	

