"""Public error codes for server-managed member features."""

SAFE_SERVICE_ERRORS = {
    'service_not_configured', 'service_rate_limited', 'service_busy',
    'service_unavailable', 'service_invalid_input', 'reference_url_invalid',
    'reference_url_unavailable', 'reference_redirect_refused',
    'reference_too_large', 'reference_empty',
}


class ServiceError(ValueError):
    """Never include provider exceptions, account identifiers or credentials."""

