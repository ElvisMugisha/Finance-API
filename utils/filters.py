import django_filters
from django.db.models import Q
from accounts.models import Transaction


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
