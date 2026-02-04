"""
Tests for sanitization utilities.
"""

from tosca_api.apps.core.sanitization import (
    sanitize_simple,
    sanitize_rich,
    sanitize_content
)


def test_sanitize_simple_strips_tags():
    """Test that sanitize_simple removes all HTML tags."""
    unsafe = "<p>Hello <b>World</b></p><script>alert(1)</script>"

    result = sanitize_simple(unsafe)
    assert "<p>" not in result
    assert "<b>" not in result
    assert "<script>" not in result
    assert result == "Hello World"


def test_sanitize_rich_removes_script():
    """Test that rich sanitization strips script tags."""
    unsafe = "<script>alert(1)</script><p>Safe</p>"
    result = sanitize_rich(unsafe)
    assert "<script>" not in result
    assert "alert(1)" not in result
    assert "<p>Safe</p>" in result


def test_sanitize_rich_removes_event_handlers():
    """Test rejection of onerror/onclick."""
    unsafe = '<img src="x" onerror="alert(1)" alt="test">'
    result = sanitize_rich(unsafe)
    assert 'onerror' not in result
    assert '<img' in result
    assert 'src="x"' in result
    assert 'alt="test"' in result


def test_sanitize_rich_removes_javascript_urls():
    """Test rejection of javascript: hrefs."""
    unsafe = '<a href="javascript:alert(1)">Click me</a>'
    result = sanitize_rich(unsafe)
    assert 'javascript:' not in result
    assert 'Click me' in result


def test_sanitize_rich_preserves_allowlist():
    """Test that allowed tags pass through."""
    safe = '<h1>Title</h1><p><strong>Bold</strong></p>'
    assert sanitize_rich(safe) == safe


def test_sanitize_content_router():
    """Test the router function."""
    assert sanitize_content("<b>Hi</b>", "simple") == "Hi"
    assert sanitize_content("<b>Hi</b>", "rich") == "<b>Hi</b>"
