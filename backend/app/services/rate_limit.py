"""In-memory token-bucket per Telegram chat_id. 30 events / 60s by default."""
import time
from collections import defaultdict, deque
from ..config import settings

_window_seconds = 60
_buckets: dict[str, deque[float]] = defaultdict(deque)


def allow(chat_id: str | int, limit: int | None = None) -> bool:
    """Returns True if the call is within the limit, False if rate-limited."""
    if chat_id is None:
        return True
    key = str(chat_id)
    cap = limit if limit is not None else settings.RATE_LIMIT_PER_MIN
    now = time.monotonic()
    q = _buckets[key]
    while q and (now - q[0]) > _window_seconds:
        q.popleft()
    if len(q) >= cap:
        return False
    q.append(now)
    return True


def reset(chat_id: str | int) -> None:
    _buckets.pop(str(chat_id), None)
