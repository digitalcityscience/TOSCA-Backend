"""
OpenAPI schema customization for drf-spectacular.

This module provides hooks to add common error responses to all API endpoints.
"""

# Common error response schemas
ERROR_RESPONSES = {
    "400": {
        "description": "Bad Request - Validation error or malformed request",
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "detail": {"type": "string", "example": "Invalid input."},
                        "field_errors": {
                            "type": "object",
                            "additionalProperties": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "example": {"title": ["This field is required."]},
                        },
                    },
                }
            }
        },
    },
    "401": {
        "description": "Unauthorized - Authentication credentials were not provided or are invalid",
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "detail": {
                            "type": "string",
                            "example": "Authentication credentials were not provided.",
                        }
                    },
                }
            }
        },
    },
    "403": {
        "description": "Forbidden - You do not have permission to perform this action",
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "detail": {
                            "type": "string",
                            "example": "You do not have permission to perform this action.",
                        }
                    },
                }
            }
        },
    },
    "404": {
        "description": "Not Found - The requested resource does not exist",
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "detail": {"type": "string", "example": "Not found."}
                    },
                }
            }
        },
    },
    "500": {
        "description": "Internal Server Error - An unexpected error occurred",
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "detail": {
                            "type": "string",
                            "example": "A server error occurred.",
                        }
                    },
                }
            }
        },
    },
}

# Map HTTP methods to their typical error responses
METHOD_RESPONSES = {
    "get": ["401", "403", "404", "500"],
    "post": ["400", "401", "403", "500"],
    "put": ["400", "401", "403", "404", "500"],
    "patch": ["400", "401", "403", "404", "500"],
    "delete": ["401", "403", "404", "500"],
}


def add_common_responses(result, generator, request, public):
    """
    Postprocessing hook to add common error responses to all API endpoints.

    This ensures ReDoc/Swagger shows 4xx and 5xx responses for all endpoints.
    """
    paths = result.get("paths", {})

    for path, methods in paths.items():
        for method, operation in methods.items():
            if method not in METHOD_RESPONSES:
                continue

            # Get existing responses or create empty dict
            responses = operation.get("responses", {})

            # Add common error responses for this method
            for status_code in METHOD_RESPONSES[method]:
                if status_code not in responses:
                    responses[status_code] = ERROR_RESPONSES[status_code]

            operation["responses"] = responses

    return result
