from rest_framework.pagination import PageNumberPagination


class OptionalPageNumberPagination(PageNumberPagination):
    """Page-number pagination that only kicks in when the client asks for it.

    The web SPA and the mobile app historically read several list endpoints as
    bare arrays. To add pagination without breaking that contract, this paginator
    returns ``None`` (→ the view serialises the full queryset as a plain array)
    unless the request carries a ``page`` or ``page_size`` query param, in which
    case it returns the usual ``{count, next, previous, results}`` envelope.
    """

    page_size_query_param = "page_size"
    max_page_size = 200

    def paginate_queryset(self, queryset, request, view=None):
        params = request.query_params
        if "page" not in params and "page_size" not in params:
            return None
        return super().paginate_queryset(queryset, request, view=view)
