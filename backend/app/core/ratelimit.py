import time

from valkey.asyncio import Valkey


async def allow(valkey: Valkey, key: str, limit: int, window_seconds: int = 60) -> bool:
    """Fixed-window counter. True while `key` is within `limit` hits for this window."""
    bucket = int(time.time()) // window_seconds
    counter_key = f"rl:{key}:{bucket}"
    count = await valkey.incr(counter_key)
    if count == 1:
        await valkey.expire(counter_key, window_seconds)
    return int(count) <= limit
