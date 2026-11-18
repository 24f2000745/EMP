"""Small Redis cache adapter. The portal remains usable if Redis is offline."""
import json, os
_client = None

def client():
    global _client
    if _client is None:
        import redis
        _client = redis.Redis.from_url(os.environ.get('REDIS_URL','redis://localhost:6379/0'), decode_responses=True, socket_connect_timeout=1)
    return _client

def cache_get(key):
    try:
        value=client().get('emp:'+key)
        return json.loads(value) if value else None
    except Exception: return None

def cache_set(key,value,ttl=60):
    try: client().setex('emp:'+key,ttl,json.dumps(value,default=str))
    except Exception: pass

def cache_clear(prefix=None):
    try:
        pattern='emp:'+((prefix or '')+'*')
        keys=list(client().scan_iter(match=pattern,count=200))
        if keys: client().delete(*keys)
    except Exception: pass
