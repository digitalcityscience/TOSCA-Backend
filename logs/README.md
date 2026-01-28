# Logs Directory

This directory contains application log files generated during runtime.

## Log Files

- `tosca_api.log` - General application logs (INFO level and above)
- `security.log` - Security-related events (authentication, authorization)
- `errors.log` - Error-level events and exceptions

## Log Levels

- **DEBUG**: Detailed diagnostic information (development only)
- **INFO**: General information about system operation
- **WARNING**: Something unexpected happened but system continues
- **ERROR**: Due to a serious problem, the software couldn't perform a function
- **CRITICAL**: A serious error occurred that may prevent the program from continuing

## Configuration

Logging configuration is defined in:

- `tosca_api/settings/base.py` - Base logging setup
- `tosca_api/settings/development.py` - Development overrides (DEBUG level)
- `tosca_api/settings/production.py` - Production configuration (file-based, JSON format)

## Security

- Log files may contain sensitive information
- Never commit log files to version control
- Regularly rotate and archive log files in production
- Monitor security logs for unusual patterns
