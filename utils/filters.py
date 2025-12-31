import django_filters
from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework.request import Request

from utils import loggings

logger = loggings.setup_logging()
User = get_user_model()


class UserFilter(django_filters.FilterSet):
    """
    Advanced filtering for users using django-filter.
    """

    search = django_filters.CharFilter(method="filter_search")
    ordering = django_filters.OrderingFilter(
        fields=(
            ("created_at", "created_at"),
            ("last_login", "last_login"),
            ("email", "email"),
            ("first_name", "first_name"),
            ("last_name", "last_name"),
        )
    )

    class Meta:
        model = User
        fields = {
            "is_active": ["exact"],
            "is_staff": ["exact"],
            "is_superuser": ["exact"],
            "is_verified": ["exact"],
            "is_premium": ["exact"],
        }

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(username__icontains=value)
            | Q(email__icontains=value)
            | Q(first_name__icontains=value)
            | Q(middle_name__icontains=value)
            | Q(last_name__icontains=value)
        )


class CurrencyFilter(django_filters.FilterSet):
    """
    Advanced filtering for currencies using django-filter.
    """

    search = django_filters.CharFilter(method="filter_search")
    ordering = django_filters.OrderingFilter(
        fields=(
            ("code", "code"),
            ("name", "name"),
            ("exchange_rate", "exchange_rate"),
            ("updated_at", "updated_at"),
        )
    )

    class Meta:
        from core.models import Currency

        model = Currency
        fields = {
            "is_active": ["exact"],
            "is_base_currency": ["exact"],
        }

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(code__icontains=value)
            | Q(name__icontains=value)
            | Q(symbol__icontains=value)
        )


class CategoryFilter:
    """
    Encapsulates category filtering logic.
    Keeping this as is for now because it has complex custom logic for system/mine views,
    but standardizing the method name.
    """

    def __init__(
        self,
        request: Request,
        queryset,
        is_system=False,
        is_mine=False,
    ):
        self.request = request
        self.queryset = queryset
        self.user = request.user
        self.is_system = is_system
        self.is_mine = is_mine

    def apply(self):
        """Standardized apply method."""
        self._apply_search()
        self._apply_category_type_filter()
        self._apply_parent_filter()

        if self.is_system:
            self._apply_system_filters()
        elif self.is_mine:
            self._apply_mine_filters()
        else:
            if self.user.is_staff or self.user.is_superuser:
                self._apply_staff_filters()
            else:
                self._apply_regular_user_filters()

        if not self.request.query_params.get("include_inactive"):
            self.queryset = self.queryset.filter(is_active=True)

        self._apply_ordering()
        return self.queryset

    def _apply_ordering(self):
        ordering = self.request.query_params.get("ordering")
        allowed_fields = ["name", "transaction_count", "created_at", "updated_at"]

        if ordering:
            orders = []
            for field in ordering.split(","):
                field_name = field.lstrip("-")
                if field_name in allowed_fields:
                    orders.append(field)
            if orders:
                self.queryset = self.queryset.order_by(*orders)
                return

        self.queryset = self.queryset.order_by("name")

    def _apply_search(self) -> None:
        search_query = self.request.query_params.get("search")
        if search_query:
            self.queryset = self.queryset.filter(name__icontains=search_query)

    def _apply_category_type_filter(self) -> None:
        category_type = self.request.query_params.get("category_type")
        if category_type:
            self.queryset = self.queryset.filter(category_type=category_type)

    def _apply_parent_filter(self) -> None:
        parent_id = self.request.query_params.get("parent_id")
        if parent_id:
            try:
                self.queryset = self.queryset.filter(parent_id=parent_id)
            except (ValueError, TypeError):
                pass

    def _apply_system_filters(self) -> None:
        self.queryset = self.queryset.filter(is_system_category=True)

    def _apply_mine_filters(self) -> None:
        self.queryset = self.queryset.filter(user=self.user)

    def _apply_staff_filters(self) -> None:
        include_inactive = self.request.query_params.get("include_inactive")
        if not (include_inactive and include_inactive.lower() == "true"):
            self.queryset = self.queryset.filter(is_active=True)

    def _apply_regular_user_filters(self) -> None:
        self.queryset = self.queryset.filter(
            Q(user=self.user) | Q(is_system_category=True)
        )
