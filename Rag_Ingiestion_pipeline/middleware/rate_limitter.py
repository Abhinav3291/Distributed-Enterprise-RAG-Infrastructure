import os
import time
from dotenv import load_dotenv
import redis

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.abspath(os.path.join(CURRENT_DIR, "..", ".env"))
load_dotenv(dotenv_path=ENV_PATH, override=True)


class BucketRateLimiter:

    def __init__(
        self,
        capacity: float = 10.0,
        refill_rate: float = 1.0,
        default_ttl: int = 86400,
    ):
        """
        Token Bucket Rate Limiter using atomic Redis Lua scripting.

        :param capacity: Maximum tokens (burst limit)
        :param refill_rate: Tokens added per second
        :param default_ttl: Expiration for inactive rate limit keys in Redis (seconds)
        """
        self.host = os.environ.get("REDIS_HOST", "localhost")
        self.port = int(os.environ.get("REDIS_PORT", 6379))
        self.password = os.environ.get("REDIS_PASS", None)
        self.db = int(os.environ.get("REDIS_DB", 0))

        self.r = redis.Redis(
            host=self.host,
            port=self.port,
            password=self.password,
            db=self.db,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,  # Added to prevent blocking indefinitely on read/write hanging
        )

        self.capacity = float(capacity)
        self.refill_rate = float(refill_rate)
        self.default_ttl = default_ttl

        # Atomic Token Bucket Lua Script
        lua_script = """
        local key = KEYS[1]
        local capacity = tonumber(ARGV[1])
        local refill_rate = tonumber(ARGV[2])
        local now = tonumber(ARGV[3])
        local ttl = tonumber(ARGV[4])

        local data = redis.call("HMGET", key, "tokens", "last_updated")
        local tokens = tonumber(data[1])
        local last_updated = tonumber(data[2])

        if not tokens or not last_updated then
            tokens = capacity
            last_updated = now
        else
            local elapsed = now - last_updated
            local refill = elapsed * refill_rate
            tokens = math.min(capacity, tokens + refill)
        end

        if tokens >= 1.0 then
            tokens = tokens - 1.0
            redis.call("HSET", key, "tokens", tokens, "last_updated", now)
            redis.call("EXPIRE", key, ttl)
            return {1, math.floor(tokens)} 
        else
            local time_to_wait = (1.0 - tokens) / refill_rate
            -- Refresh key TTL even when blocked to maintain state during high-frequency requests
            redis.call("EXPIRE", key, ttl)
            return {0, math.ceil(time_to_wait)} 
        end
        """
        self.rate_limit_script = self.r.register_script(lua_script)

    def check_rate_limit(self, identifier: str):
        """
        Checks if an identifier (user_id / IP) can consume 1 token.
        """
        key = f"rate_limit:{identifier}"
        try:
            now = time.time()
            result = self.rate_limit_script(
                keys=[key],
                args=[self.capacity, self.refill_rate, now, self.default_ttl],
            )

            is_allowed = bool(result[0])
            val = result[1]

            if is_allowed:
                return True, {"allowed": True, "remaining_tokens": val}
            else:
                return False, {
                    "allowed": False,
                    "remaining_tokens": 0,
                    "retry_after_seconds": val,
                }

        except redis.RedisError as e:
            print(f"[RateLimiter Warning] Redis error (failing open): {e}")
            return True, {
                "allowed": True,
                "remaining_tokens": 1,
                "warning": "Rate limit bypassed due to Redis error",
            }


if __name__ == "__main__":
    print("--- Testing Token Bucket Rate Limiter ---")
    limiter = BucketRateLimiter(capacity=3.0, refill_rate=0.5)
    test_user = "user_dev_01"

    print(f"Executing 5 rapid requests for '{test_user}'...")
    for i in range(1, 6):
        allowed, res = limiter.check_rate_limit(test_user)
        if allowed:
            print(f"Request {i}: ALLOWED | Remaining Tokens: {res['remaining_tokens']}")
        else:
            print(f"Request {i}: BLOCKED | Retry After: {res['retry_after_seconds']}s")
        time.sleep(0.1)