"""
Geodata Engine Exceptions
"""


class GeodataEngineError(Exception):
    """Base exception for geodata engine operations"""
    pass


class PublishingError(GeodataEngineError):
    """Exception raised when publishing operations fail"""
    pass


class DataImportError(GeodataEngineError):
    """Exception raised when data import operations fail"""
    pass


class DatabaseIntrospectionError(GeodataEngineError):
    """Exception raised when database introspection fails"""
    pass


class GeoServerConnectionError(PublishingError):
    """Exception raised when GeoServer connection fails"""
    pass


class GeoServerPublishError(PublishingError):
    """Exception raised when GeoServer publishing fails"""
    pass