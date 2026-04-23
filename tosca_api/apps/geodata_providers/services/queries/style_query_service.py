class StyleQueryService:
    """Placeholder query boundary for style catalog reads."""

    @classmethod
    def list_styles(cls) -> list[dict]:
        return []

    @classmethod
    def get_style_detail(cls, *, style_id) -> dict:
        raise NotImplementedError(
            "StyleQueryService.get_style_detail is not implemented yet because the style domain is not defined."
        )
