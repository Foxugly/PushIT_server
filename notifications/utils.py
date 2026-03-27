import hashlib
import json

from django.core.serializers.json import DjangoJSONEncoder

def compute_request_fingerprint(data: dict) -> str:
    raw = json.dumps(data, sort_keys=True, separators=(",", ":"), cls=DjangoJSONEncoder)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
