import hashlib
import json

def compute_request_fingerprint(data: dict) -> str:
    raw = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()