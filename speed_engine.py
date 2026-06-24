from functools import wraps
from time import time


def ttl_cache(seconds: int = 60):
    """Small in-process TTL cache for fast repeated dashboard calls."""
    store = {}

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            key = (func.__name__, args, tuple(sorted(kwargs.items())))
            now = time()
            if key in store:
                saved_at, value = store[key]
                if now - saved_at < seconds:
                    return value
            value = func(*args, **kwargs)
            store[key] = (now, value)
            return value
        return wrapper
    return decorator
