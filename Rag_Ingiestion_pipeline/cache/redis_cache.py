import hashlib
import json
import os
from typing import Any, Optional
from dotenv import load_dotenv
import redis

# Load .env from parent directory
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.abspath(os.path.join(CURRENT_DIR, "..", ".env"))
load_dotenv(dotenv_path=ENV_PATH, override=True)


class RedisCacheManager:

    def __init__(self, default_ttl: int = 3600):
        self.host = os.environ.get("REDIS_HOST", "localhost")
        self.port = int(os.environ.get("REDIS_PORT", 6379))
        self.password = os.environ.get("REDIS_PASS", None)
        self.db = int(os.environ.get("REDIS_DB", 0))
        self.default_ttl = int(os.environ.get("CACHE_TTL", default_ttl))

        # Added socket_timeout to prevent infinite blocking on read/write stalls
        self.client = redis.Redis(
            host=self.host,
            port=self.port,
            password=self.password,
            db=self.db,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
        )

    def generate_query_key(
        self,
        query: str,
        user_id: Optional[str] = None,
        doc_hash: Optional[str] = None,
        namespace: str = "rag_response",
    ) -> str:
        """
        Generates a scoped SHA256 cache key preventing cross-user / cross-doc leakage.
        """
        normalized_query = query.strip().lower()
        
        # Scope raw key by user and document hash if available
        key_raw = f"{user_id or 'global'}:{doc_hash or 'all'}:{normalized_query}"
        query_hash = hashlib.sha256(key_raw.encode("utf-8")).hexdigest()
        
        return f"{namespace}:{query_hash}"

    def get(self, key: str) -> Optional[Any]:
        """
        Retrieves and deserializes JSON payload from Redis.
        Returns None on cache miss or connection error (graceful fallback).
        """
        try:
            cached_data = self.client.get(key)
            if cached_data:
                print(f"[Redis Cache] HIT for key: {key}")
                return json.loads(cached_data)
            print(f"[Redis Cache] MISS for key: {key}")
            return None
        except Exception as e:
            print(f"[Redis Cache Error] GET failed (falling back to RAG pipeline): {e}")
            return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """Serializes and stores data in Redis with an expiration TTL."""
        try:
            expire_time = ttl if ttl is not None else self.default_ttl
            serialized_value = json.dumps(value)
            self.client.set(name=key, value=serialized_value, ex=expire_time)
            print(f"[Redis Cache] SET key: {key} (TTL: {expire_time}s)")
            return True
        except Exception as e:
            print(f"[Redis Cache Error] SET failed: {e}")
            return False

    def delete(self, key: str) -> bool:
        """Deletes a key from Redis cache."""
        try:
            self.client.delete(key)
            print(f"[Redis Cache] DELETED key: {key}")
            return True
        except Exception as e:
            print(f"[Redis Cache Error] DELETE failed: {e}")
            return False

    def is_healthy(self) -> bool:
        """Pings Redis server to verify connectivity."""
        try:
            return self.client.ping()
        except Exception:
            return False


# --- Standalone Verification Test ---
if __name__ == "__main__":
    print("--- Testing Redis Cache Manager ---")
    cache = RedisCacheManager()

    if cache.is_healthy():
        print("[+] Successfully connected to Redis!")

        # 1. Test Scoped Key Generation
        test_query = "How does vector indexing work in ChromaDB?"
        cache_key = cache.generate_query_key(
            query=test_query, 
            user_id="user_dev_01", 
            doc_hash="abc123hash"
        )
        print(f"Generated Scoped Cache Key: {cache_key}")

        # 2. Test SET
        mock_response = {
            "query": test_query,
            "answer": "ChromaDB uses HNSW graphs for fast vector similarity search.",
            "source_documents": ["Abhinav_Resume_2026.pdf"],
        }
        cache.set(cache_key, mock_response, ttl=60)

        # 3. Test GET
        retrieved_payload = cache.get(cache_key)
        print("Retrieved Cached Output:", retrieved_payload)

    else:
        print("[!] Could not connect to Redis container on localhost:6379.")