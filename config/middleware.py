import uuid

from .metrics import increment_counter
from .logging_utils import reset_incident_id, reset_request_id, set_incident_id, set_request_id


class RequestIdMiddleware:
    header_name = "HTTP_X_REQUEST_ID"
    response_header = "X-Request-ID"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.META.get(self.header_name) or str(uuid.uuid4())
        request.request_id = request_id
        request_token = set_request_id(request_id)
        incident_token = set_incident_id("-")

        try:
            response = self.get_response(request)
        finally:
            reset_incident_id(incident_token)
            reset_request_id(request_token)

        response[self.response_header] = request_id
        return response


class MetricsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        resolver_match = getattr(request, "resolver_match", None)
        route = getattr(resolver_match, "view_name", None) or request.path
        increment_counter(
            "pushit_http_requests_total",
            labels={
                "method": request.method,
                "route": route,
                "status": response.status_code,
            },
        )
        return response
