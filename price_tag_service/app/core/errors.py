from __future__ import annotations


class ServiceError(Exception):
    error_type = "UNKNOWN_ERROR"


class ConfigurationError(ServiceError):
    error_type = "CONFIG_ERROR"


class ExternalApiError(ServiceError):
    error_type = "EXTERNAL_API_ERROR"


class NonRetryableApiError(ExternalApiError):
    error_type = "NON_RETRYABLE_API_ERROR"


class ResponseInvalidError(ExternalApiError):
    error_type = "RESPONSE_INVALID"
