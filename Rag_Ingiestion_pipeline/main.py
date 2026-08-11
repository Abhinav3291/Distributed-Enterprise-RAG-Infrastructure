import os
import sys
import shutil
import asyncio
from fastapi import FastAPI, HTTPException, status, UploadFile, File
from pydantic import BaseModel, Field

# Ensure internal modules import smoothly
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(CURRENT_DIR)

from cache.redis_cache import RedisCacheManager
from guardrails.input_guardrails import InputGuardrails
from guardrails.output_guardrails import OutputGuardrails
from middleware.rate_limitter import BucketRateLimiter
from vector_engine import VectorEngine

app = FastAPI(
    title="Enterprise RAG Engine",
    description="Streamlined In-Memory Async Gateway for Enterprise RAG Operations",
    version="2.1.0",
)

# Global Service Components
cache = RedisCacheManager()
rate_limiter = BucketRateLimiter(capacity=10.0, refill_rate=1.0)
input_guardrails = InputGuardrails()
output_guardrails = OutputGuardrails(groundedness_threshold=0.10)
_vector_engine = VectorEngine()

# Concurrency Throttle: Limits simultaneous heavy RAG tasks (Vector DB + LLM)
MAX_CONCURRENT_RAG_TASKS = 10
rag_semaphore = asyncio.Semaphore(MAX_CONCURRENT_RAG_TASKS)


class QueryRequest(BaseModel):
    user_id: str = Field(..., example="user_dev_01")
    query: str = Field(..., example="How does vector indexing work in ChromaDB?")


@app.post("/query")
async def process_rag_query(payload: QueryRequest):
    """
    Complete RAG Pipeline with Input & Output Guardrails:
    1. Input Guardrail Sanitization (PII Scrubbing & Prompt Injection Check)
    2. Token Bucket Rate Limiting Check
    3. Redis Cache Lookup (Cache Hit)
    4. Throttled Vector Search & LLM Generation (Cache Miss)
    5. Output Guardrail Validation (Groundedness & PII Output Redaction)
    6. Cache Response & Return Payload
    """
    # Step 1: Input Guardrail Check
    is_safe, guardrail_result = input_guardrails.validate_input(payload.query)
    if not is_safe:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Input Security Guardrail Block: {guardrail_result}",
        )
    sanitized_query = guardrail_result

    # Step 2: Rate Limiter Check
    allowed, rate_meta = rate_limiter.check_rate_limit(payload.user_id)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. Retry after {rate_meta.get('retry_after_seconds', 1)} seconds.",
        )

    # Step 3: Cache Pre-Check
    cache_key = cache.generate_query_key(sanitized_query)
    cached_data = await asyncio.to_thread(cache.get, cache_key)
    if cached_data:
        return {
            "source": "CACHE_HIT",
            "status": "COMPLETED",
            "result": cached_data,
        }

    # Step 4 & 5: Throttled Heavy RAG Execution & Output Guardrails
    async with rag_semaphore:
        try:
            raw_rag_response = await asyncio.to_thread(
                _vector_engine.query, sanitized_query
            )

            raw_answer = raw_rag_response.get("answer", "")
            context_chunks = raw_rag_response.get("context_chunks", [])

            sanitized_answer, is_valid_output, status_code_reason = (
                output_guardrails.validate_and_sanitize(raw_answer, context_chunks)
            )

            if not is_valid_output:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "error": "Output failed safety or grounding validation.",
                        "reason": status_code_reason,
                        "message": sanitized_answer,
                    },
                )

            final_result = {
                "query": sanitized_query,
                "answer": sanitized_answer,
                "sources": [c.get("metadata", {}).get("filename", "unknown") for c in context_chunks],
            }

            await asyncio.to_thread(cache.set, cache_key, final_result)

            return {
                "source": "RAG_PIPELINE",
                "status": "COMPLETED",
                "result": final_result,
            }

        except HTTPException as he:
            raise he
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error executing RAG pipeline: {str(e)}",
            )


@app.post("/upload")
async def upload_document(file: UploadFile = File(...)):
    if not file.filename.endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF files are supported.",
        )

    docx_dir = os.path.join(CURRENT_DIR, "Docx")
    os.makedirs(docx_dir, exist_ok=True)
    file_path = os.path.join(docx_dir, file.filename)

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        await asyncio.to_thread(_vector_engine.ingest_pdf, file_path)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to ingest document: {e}",
        )

    return {
        "status": "COMPLETED",
        "message": f"Successfully uploaded and ingested {file.filename}.",
    }


@app.get("/health")
async def system_health_check():
    redis_healthy = await asyncio.to_thread(cache.is_healthy)
    return {
        "status": "OPERATIONAL" if redis_healthy else "DEGRADED",
        "components": {
            "redis_cache": "CONNECTED" if redis_healthy else "OFFLINE",
            "chroma_vector_db": "READY",
        },
    }