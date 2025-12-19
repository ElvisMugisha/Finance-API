import django_filters
from django.db.models import Q
from django.db import models
from django.contrib.auth import get_user_model
from rest_framework.request import Request

from accounts.models import Transaction

from utils import loggings

logger = loggings.setup_logging()
User = get_user_model()


class UserFilter:
    """
    Encapsulates advanced user filtering logic for API endpoints.

    Supports:
    - Filtering by active status, staff, superuser, verified, premium
    - Searching by email, username, or full name
    - Ordering if needed
    - Safe for production: logs errors without exposing sensitive info
    """

    def __init__(self, request: Request, queryset=None):
        self.request = request
        self.queryset = queryset if queryset is not None else User.objects.all()
        self.user = request.user

    def apply(self):
        """
        Apply all filters, search, and ordering to the queryset.
        Returns a filtered queryset.
        """
        try:
            self._apply_basic_filters()
            self._apply_search()
            self._apply_ordering()
            return self.queryset

        except Exception as e:
            logger.exception(f"UserFilter.apply failed: {str(e)}")
            return self.queryset.none()  # Safe fallback

    def _apply_basic_filters(self):
        """Filter by standard query params like is_active, is_staff, etc."""
        for field in [
            "is_active",
            "is_staff",
            "is_superuser",
            "is_verified",
            "is_premium",
        ]:
            value = self.request.query_params.get(field)
            if value is not None:
                if value.lower() in ["true", "1"]:
                    self.queryset = self.queryset.filter(**{field: True})
                elif value.lower() in ["false", "0"]:
                    self.queryset = self.queryset.filter(**{field: False})
                logger.debug(f"Applied filter {field}={value}")

    def _apply_search(self):
        """Apply search across email, username, first/middle/last name."""
        search_query = self.request.query_params.get("search")
        if search_query:
            self.queryset = self.queryset.filter(
                Q(username__icontains=search_query)
                | Q(email__icontains=search_query)
                | Q(first_name__icontains=search_query)
                | Q(middle_name__icontains=search_query)
                | Q(last_name__icontains=search_query)
            )
            logger.debug(f"Applied search filter: {search_query}")

    def _apply_ordering(self):
        """Apply ordering from query params, default by -created_at."""
        ordering = self.request.query_params.get("ordering")
        allowed_fields = [
            "created_at",
            "last_login",
            "email",
            "first_name",
            "last_name",
        ]

        if ordering:
            orders = []
            for field in ordering.split(","):
                field_name = field.lstrip("-")
                if field_name in allowed_fields:
                    orders.append(field)
            if orders:
                self.queryset = self.queryset.order_by(*orders)
                logger.debug(f"Applied ordering: {orders}")
        else:
            self.queryset = self.queryset.order_by("-created_at")


class TransactionFilter(django_filters.FilterSet):
    """Advanced filtering for transactions."""

    start_date = django_filters.DateFilter(
        field_name="transaction_date", lookup_expr="gte"
    )
    end_date = django_filters.DateFilter(
        field_name="transaction_date", lookup_expr="lte"
    )
    min_amount = django_filters.NumberFilter(field_name="amount", lookup_expr="gte")
    max_amount = django_filters.NumberFilter(field_name="amount", lookup_expr="lte")

    search = django_filters.CharFilter(method="filter_search")

    class Meta:
        model = Transaction
        fields = [
            "transaction_type",
            "status",
            "account",
            "category",
            "is_transfer",
            "is_recurring",
        ]

    def filter_search(self, queryset, name, value):
        """Search across multiple fields."""
        return queryset.filter(
            Q(name__icontains=value)
            | Q(description__icontains=value)
            | Q(merchant__icontains=value)
            | Q(reference_number__icontains=value)
        )


class CategoryFilter:
    """
    Encapsulates category filtering logic.

    Implements:
    - Search by name
    - Filter by category type
    - Filter by parent
    - User-specific filtering
    """

    def __init__(
        self,
        request: Request,
        queryset: models.QuerySet,
        is_system=False,
        is_mine=False,
    ):
        self.request = request
        self.queryset = queryset
        self.user = request.user
        self.is_system = is_system
        self.is_mine = is_mine

    def apply_filters(self) -> models.QuerySet:
        """Apply all filters to the queryset."""
        # Apply basic filters that work for all endpoints
        self._apply_search()
        self._apply_category_type_filter()
        self._apply_parent_filter()

        # Apply endpoint-specific filters
        if self.is_system:
            self._apply_system_filters()
        elif self.is_mine:
            self._apply_mine_filters()
        else:
            # For list view, apply user-specific filters
            if self.user.is_staff or self.user.is_superuser:
                self._apply_staff_filters()
            else:
                self._apply_regular_user_filters()

        # Apply active filter if not overridden
        if not self.request.query_params.get("include_inactive"):
            self.queryset = self.queryset.filter(is_active=True)

        return self.queryset.order_by("name")

    def _apply_search(self) -> None:
        """Apply search by name."""
        search_query = self.request.query_params.get("search")
        if search_query:
            self.queryset = self.queryset.filter(name__icontains=search_query)
            logger.debug(f"Applied search filter: {search_query}")

    def _apply_category_type_filter(self) -> None:
        """Filter by category type."""
        category_type = self.request.query_params.get("category_type")
        if category_type:
            self.queryset = self.queryset.filter(category_type=category_type)
            logger.debug(f"Applied category_type filter: {category_type}")

    def _apply_parent_filter(self) -> None:
        """Filter by parent category."""
        parent_id = self.request.query_params.get("parent_id")
        if parent_id:
            try:
                self.queryset = self.queryset.filter(parent_id=parent_id)
                logger.debug(f"Applied parent filter: {parent_id}")
            except (ValueError, TypeError) as e:
                logger.warning(f"Invalid parent_id: {parent_id}, error: {e}")

    def _apply_system_filters(self) -> None:
        """Apply filters for system categories endpoint."""
        # System endpoint only shows system categories
        self.queryset = self.queryset.filter(is_system_category=True)
        logger.debug("Applied system category filter")

    def _apply_mine_filters(self) -> None:
        """Apply filters for my categories endpoint."""
        # Mine endpoint only shows current user's categories
        self.queryset = self.queryset.filter(user=self.user)
        logger.debug(f"Applied my categories filter for user {self.user.id}")

    def _apply_staff_filters(self) -> None:
        """Apply filters for staff/admin users."""
        # Staff can see all categories
        include_inactive = self.request.query_params.get("include_inactive")

        if include_inactive and include_inactive.lower() == "true":
            # Include all categories for staff/admin
            logger.debug("Staff/admin viewing all categories (including inactive)")
        else:
            # Default: show only active categories
            self.queryset = self.queryset.filter(is_active=True)
            logger.debug("Staff/admin viewing active categories only")

    def _apply_regular_user_filters(self) -> None:
        """Apply filters for regular users."""
        # Regular users see their categories + system categories
        self.queryset = self.queryset.filter(
            models.Q(user=self.user) | models.Q(is_system_category=True)
        )
        logger.debug(f"Regular user {self.user.id} viewing filtered categories")
