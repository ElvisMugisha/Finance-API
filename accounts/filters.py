import django_filters
from django.db import models
from django.db.models import Q
from rest_framework.request import Request

from accounts.models import (
    Account,
    Budget,
    BudgetCategory,
    FinancialGoal,
    Report,
    Transaction,
)
from utils import loggings

logger = loggings.setup_logging()


class AccountFilter(django_filters.FilterSet):
    """
    Advanced filtering for accounts using django-filter.
    """

    class Meta:
        model = Account
        fields = {
            "account_type": ["exact"],
            "is_active": ["exact"],
            "is_primary": ["exact"],
            "currency": ["exact"],
            "bank_name": ["icontains"],
        }


class TransactionFilter(django_filters.FilterSet):
    """
    Advanced filtering for transactions using django-filter.
    """

    start_date = django_filters.DateFilter(
        field_name="transaction_date", lookup_expr="gte"
    )
    end_date = django_filters.DateFilter(
        field_name="transaction_date", lookup_expr="lte"
    )
    min_amount = django_filters.NumberFilter(field_name="amount", lookup_expr="gte")
    max_amount = django_filters.NumberFilter(field_name="amount", lookup_expr="lte")

    class Meta:
        model = Transaction
        fields = {
            "transaction_type": ["exact"],
            "status": ["exact"],
            "category": ["exact"],
            "account": ["exact"],
            "is_transfer": ["exact"],
            "is_tax_deductible": ["exact"],
            "is_recurring": ["exact"],
            "merchant": ["icontains"],
            "reference_number": ["icontains"],
        }


class BudgetFilter(django_filters.FilterSet):
    """
    Advanced filtering for budgets using django-filter.
    """

    class Meta:
        model = Budget
        fields = {
            "budget_type": ["exact"],
            "is_active": ["exact"],
            "category": ["exact"],
        }


class FinancialGoalFilter(django_filters.FilterSet):
    """
    Advanced filtering for financial goals using django-filter.
    """

    class Meta:
        model = FinancialGoal
        fields = {
            "goal_type": ["exact"],
            "is_active": ["exact"],
            "is_achieved": ["exact"],
            "priority": ["exact"],
        }


class BudgetCategoryFilter(django_filters.FilterSet):
    """
    Advanced filtering for budget categories using django-filter.
    """

    class Meta:
        model = BudgetCategory
        fields = {
            "budget": ["exact"],
            "category": ["exact"],
        }
