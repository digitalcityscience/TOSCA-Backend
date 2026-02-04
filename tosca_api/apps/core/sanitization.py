"""
HTML Sanitization Utilities.

This module provides functions to sanitize HTML content using the `nh3` library
(Rust-based, fast, and secure). It supports different levels of sanitization
based on the context (simple text vs. rich HTML).
"""

import nh3

# Allowlist for rich content (based on decisions.md and tasks.md)
ALLOWED_TAGS = {
    "p", "br", "strong", "em", "b", "i", "u",
    "a", "ul", "ol", "li",
    "h1", "h2", "h3", "h4",
    "blockquote", "pre", "code",
    "img", "figure", "figcaption",
}

ALLOWED_ATTRS = {
    "a": {"href", "title", "target"},
    "img": {"src", "alt", "width", "height"},
}

ALLOWED_URL_SCHEMES = {"http", "https", "mailto"}


def sanitize_simple(content: str) -> str:
    """
    Strip ALL HTML tags, returning plain text only.
    
    Args:
        content: The input string to sanitize.
        
    Returns:
        The sanitized string with no HTML tags.
    """
    if not content:
        return ""
    # Empty set of tags means strip everything
    return nh3.clean(content, tags=set())


def sanitize_rich(content: str) -> str:
    """
    Allow only whitelisted HTML tags and attributes.
    Refuses inline styles, event handlers, and dangerous URL schemes (javascript:).
    
    Args:
        content: The input HTML string.
        
    Returns:
        The sanitized HTML string.
    """
    if not content:
        return ""
    
    return nh3.clean(
        content,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        link_rel=None,  # Do not force rel="noopener noreferrer" automatically unless configured? 
                        # nh3 defaults might be safe enough.
        url_schemes=ALLOWED_URL_SCHEMES,
    )


def sanitize_content(content: str, content_type: str) -> str:
    """
    Sanitize content based on its type.
    
    Args:
        content: The text content.
        content_type: 'simple' or 'rich'.
        
    Returns:
        The sanitized content.
    """
    if content_type == "rich":
        return sanitize_rich(content)
    # Default to strict sanitization for 'simple' or unknown types
    return sanitize_simple(content)
