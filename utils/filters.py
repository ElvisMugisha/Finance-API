import django_filters
from django.db.models import Q
from django.db import models
from accounts.models import Transaction

from utils import loggings

# Initialize logger
logger = loggings.setup_logging()


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
