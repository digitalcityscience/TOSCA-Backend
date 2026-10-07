"""What the public catalog API lists (external catalog ticket 04).

Visibility is presentation control, not data protection (the remote data is
public): ``PUBLIC`` + active rows are listed to everyone, ``PRIVATE`` +
active rows to signed-in users only, inactive rows to nobody. A category is
listed only when it has at least one listed item, and an item only when its
category and its service are listed.
"""

from __future__ import annotations

from django.db.models import Count, Prefetch, Q, QuerySet

from .models import Category, CategoryItem, ExternalService, Visibility


class ExternalCatalogVisibilityService:
    @staticmethod
    def _visible(prefix: str, authenticated: bool) -> Q:
        condition = Q(**{f"{prefix}is_active": True})
        if not authenticated:
            condition &= Q(**{f"{prefix}visibility": Visibility.PUBLIC})
        return condition

    @classmethod
    def services(cls, *, authenticated: bool) -> QuerySet[ExternalService]:
        return ExternalService.objects.filter(cls._visible("", authenticated)).order_by("title", "slug")

    @classmethod
    def items(cls, *, authenticated: bool) -> QuerySet[CategoryItem]:
        return (
            CategoryItem.objects.filter(cls._visible("category__", authenticated))
            .filter(cls._visible("service__", authenticated))
            .select_related("service")
            .order_by("display_order", "title", "id")
        )

    @classmethod
    def categories(cls, *, authenticated: bool) -> QuerySet[Category]:
        item_condition = cls._visible("items__service__", authenticated)
        return (
            Category.objects.filter(cls._visible("", authenticated))
            .annotate(visible_item_count=Count("items", filter=item_condition))
            .filter(visible_item_count__gt=0)
            .order_by("display_order", "title", "id")
        )

    @classmethod
    def category(cls, *, slug: str, authenticated: bool) -> Category:
        """One listed category with its listed items prefetched as ``visible_items``."""
        category = (
            cls.categories(authenticated=authenticated)
            .filter(slug=slug)
            .prefetch_related(
                Prefetch("items", queryset=cls.items(authenticated=authenticated), to_attr="visible_items")
            )
            .first()
        )
        if category is None:
            raise Category.DoesNotExist(f"Category '{slug}' not found.")
        return category
