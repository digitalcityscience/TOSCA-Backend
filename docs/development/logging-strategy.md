# Logging Strategy for TOSCA Django API

## Overview

This document outlines the logging strategy for the TOSCA Django API system. A well-structured logging approach is critical for monitoring, debugging, and maintaining security, especially in authentication and authorization flows.

## Logging Principles

### 1. **Security-First Logging**

- **NEVER** log sensitive data (passwords, tokens, personal data)
- **ALWAYS** log authentication failures and security events
- **ALWAYS** log authorization attempts and failures
- Use structured logging with consistent fields for security events

### 2. **Structured Logging**

- Use Python's `logging` module with proper log levels
- Include contextual information (user_id, request_id, timestamp)
- Use consistent formatting for similar events
- No `print()` statements in production code

### 3. **Log Levels Usage**

- **DEBUG**: Detailed diagnostic information (only in development)
- **INFO**: General information about system operation
- **WARNING**: Something unexpected happened but system continues
- **ERROR**: Due to a serious problem, the software couldn't perform a function
- **CRITICAL**: A serious error occurred that may prevent the program from continuing

## Implementation Guidelines

### Logger Configuration

Each module should have its own logger:

```python
import logging

logger = logging.getLogger(__name__)
```

### Security Events That Must Be Logged

#### Authentication Events

```python
# Successful authentication
logger.info("User authenticated", extra={
    'user_id': user.id,
    'username': user.username,
    'method': 'keycloak',
    'ip_address': request.META.get('REMOTE_ADDR')
})

# Failed authentication
logger.warning("Authentication failed", extra={
    'username': attempted_username,
    'reason': 'invalid_token',
    'ip_address': request.META.get('REMOTE_ADDR')
})

# JWT verification failures
logger.error("JWT verification failed", extra={
    'reason': 'signature_invalid',
    'issuer': token_payload.get('iss', 'unknown'),
    'ip_address': request.META.get('REMOTE_ADDR')
})
```

#### Authorization Events

```python
# Role synchronization
logger.info("User roles synchronized", extra={
    'user_id': user.id,
    'roles_added': list(new_roles),
    'roles_removed': list(removed_roles),
    'groups_count': user.groups.count()
})

# Permission denied
logger.warning("Permission denied", extra={
    'user_id': user.id,
    'requested_permission': 'can_view_layers',
    'endpoint': request.path,
    'method': request.method
})
```

#### Account Linking Events

```python
# Email conflicts (security risk)
logger.error("Email conflict detected during login", extra={
    'email': email,
    'existing_users_count': existing_users.count(),
    'keycloak_sub': sociallogin_sub,
    'action': 'login_blocked'
})

# Successful account linking
logger.info("Account linked successfully", extra={
    'user_id': user.id,
    'keycloak_sub': sub,
    'link_method': 'username_match'
})
```

### Application Events

#### System Operations

```python
# Database operations
logger.info("Layer created", extra={
    'layer_id': layer.id,
    'user_id': request.user.id,
    'layer_name': layer.name
})

# Configuration changes
logger.info("Settings updated", extra={
    'setting_key': 'KEYCLOAK_REALM',
    'updated_by': user.id
})
```

#### Performance Monitoring

```python
# Slow operations
logger.warning("Slow database query detected", extra={
    'query_time': query_time,
    'query_type': 'layer_search',
    'user_id': request.user.id
})
```

### What NOT to Log

❌ **Never log these:**

- Raw JWT tokens
- Passwords or password hashes
- Personal identifiable information (unless required for audit)
- Raw request bodies that may contain sensitive data
- Full stack traces in production (use ERROR level with sanitized messages)

### Debug Logging Guidelines

Debug logs should only be enabled in development:

```python
# Good - contextual debug information
logger.debug("Token payload structure", extra={
    'payload_keys': list(token_payload.keys()),
    'realm_access_present': 'realm_access' in token_payload,
    'user_id': user.id if user else None
})

# Bad - sensitive data exposure
logger.debug(f"Full token: {raw_token}")  # ❌ NEVER DO THIS
```

### Error Handling and Logging

```python
try:
    decoded_token = verify_and_decode_token(token)
    logger.info("Token verified successfully", extra={
        'user_sub': decoded_token.get('sub'),
        'token_type': 'access_token'
    })
except InvalidTokenError as e:
    logger.error("Token verification failed", extra={
        'error_type': e.__class__.__name__,
        'error_message': str(e),
        'token_present': bool(token)
    })
    raise AuthenticationFailed("Invalid token")
```

## Django Settings Configuration

### Base Logging Configuration

```python
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        },
        'json': {
            '()': 'pythonjsonlogger.jsonlogger.JsonFormatter',
            'format': '%(levelname)s %(asctime)s %(module)s %(message)s'
        },
    },
    'handlers': {
        'file': {
            'level': 'INFO',
            'class': 'logging.FileHandler',
            'filename': 'logs/tosca_api.log',
            'formatter': 'verbose',
        },
        'security_file': {
            'level': 'WARNING',
            'class': 'logging.FileHandler',
            'filename': 'logs/security.log',
            'formatter': 'json',
        },
        'console': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'WARNING',
    },
    'loggers': {
        'tosca_api': {
            'handlers': ['file', 'console'],
            'level': 'INFO',
            'propagate': False,
        },
        'tosca_api.apps.authentication': {
            'handlers': ['security_file', 'console'],
            'level': 'INFO',
            'propagate': False,
        },
        'django.security': {
            'handlers': ['security_file'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}
```

### Environment-Specific Settings

#### Development

- Console output with DEBUG level
- Detailed formatting for debugging
- All loggers enabled

#### Production

- File-based logging only
- INFO level and above
- JSON formatting for log aggregation
- Separate security log file

## Monitoring and Alerting

### Critical Events to Monitor

1. Authentication failures (multiple attempts)
2. JWT verification failures
3. Email conflict events during login
4. Permission escalation attempts
5. Unusual role synchronization patterns

### Log Aggregation

- Use structured JSON logs for production
- Integrate with ELK stack or similar
- Set up alerts for ERROR and CRITICAL level events
- Monitor authentication patterns and anomalies

## Code Review Checklist

When reviewing code changes, ensure:

- [ ] No `print()` statements in production code
- [ ] Proper log levels are used
- [ ] Security events are logged appropriately
- [ ] No sensitive data in log messages
- [ ] Structured logging with context information
- [ ] Error handling includes appropriate logging
- [ ] Debug logs are meaningful and safe

## Migration from Print Statements

### Current Issues to Address

1. Remove `print()` statements from geoserver plugin manager
2. Add structured logging to authentication flows
3. Implement security event logging
4. Add performance monitoring logs
5. Configure proper Django logging settings

### Implementation Priority

1. **High Priority**: Authentication and security logging
2. **Medium Priority**: Application event logging
3. **Low Priority**: Debug and performance logging

## Tools and Dependencies

Required packages:

```
python-json-logger>=2.0.0  # For JSON formatting
```

Recommended development tools:

- `django-extensions` for enhanced logging in development
- Log viewers like `rich` for enhanced console output during development

## Conclusion

This logging strategy ensures:

- **Security**: All critical security events are captured
- **Debugging**: Sufficient information for troubleshooting
- **Performance**: Monitoring of system health
- **Compliance**: Audit trails for security reviews
- **Maintainability**: Consistent patterns across the codebase

Follow these guidelines for all future development to maintain a robust, secure, and maintainable logging system.
