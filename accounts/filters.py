import django_filters
from django.db import models
from django.db.models import Q
from rest_framework.request import Request

from accounts.models import Account, Budget, FinancialGoal, Report, Transaction
from utils import loggings

logger = loggings.setup_logging()


class AccountFilter:
    """
    Advanced filtering for accounts.
    """

    def __init__(self, request: Request, queryset: models.QuerySet):
        self.request = request
        self.queryset = queryset

    def apply(self) -> models.QuerySet:
        """Apply all filters to the queryset."""
        self._apply_search()
        self._apply_basic_filters()
        self._apply_ordering()
        return self.queryset

    def _apply_basic_filters(self):
        """Filter by account_type, is_active, is_primary."""
        account_type = self.request.query_params.get("account_type")
        is_active = self.request.query_params.get("is_active")
        is_primary = self.request.query_params.get("is_primary")

        if account_type:
            self.queryset = self.queryset.filter(account_type=account_type)
        if is_active is not None:
            is_active = is_active.lower() == "true"
            self.queryset = self.queryset.filter(is_active=is_active)
        if is_primary is not None:
            is_primary = is_primary.lower() == "true"
            self.queryset = self.queryset.filter(is_primary=is_primary)

    def _apply_search(self):
        """Search by name, bank_name, account_number."""
        search = self.request.query_params.get("search")
        if search:
            self.queryset = self.queryset.filter(
                Q(name__icontains=search)
                | Q(bank_name__icontains=search)
                | Q(account_number__icontains=search)
            )

    def _apply_ordering(self):
        """Order by name, current_balance, created_at."""
        ordering = self.request.query_params.get("ordering", "-created_at")
        allowed_fields = [
            "name",
            "-name",
            "current_balance",
            "-current_balance",
            "created_at",
            "-created_at",
        ]
        if ordering not in allowed_fields:
            ordering = "-created_at"
        self.queryset = self.queryset.order_by(ordering)


class TransactionFilter:
    """
    Advanced filtering for transactions.
    """

    def __init__(self, request: Request, queryset: models.QuerySet):
        self.request = request
        self.queryset = queryset

    def apply(self) -> models.QuerySet:
        """Apply all filters to the queryset."""
        self._apply_search()
        self._apply_date_filters()
        self._apply_amount_filters()
        self._apply_type_filters()
        self._apply_ordering()
        return self.queryset

    def _apply_search(self):
        """Search by name, description, merchant, reference_number."""
        search = self.request.query_params.get("search")
        if search:
            self.queryset = self.queryset.filter(
                Q(name__icontains=search)
                | Q(description__icontains=search)
                | Q(merchant__icontains=search)
                | Q(reference_number__icontains=search)
            )

    def _apply_date_filters(self):
        """Filter by transaction_date range."""
        start_date = self.request.query_params.get("start_date")
        end_date = self.request.query_params.get("end_date")

        if start_date:
            self.queryset = self.queryset.filter(transaction_date__gte=start_date)
        if end_date:
            self.queryset = self.queryset.filter(transaction_date__lte=end_date)

    def _apply_amount_filters(self):
        """Filter by amount range."""
        min_amount = self.request.query_params.get("min_amount")
        max_amount = self.request.query_params.get("max_amount")

        if min_amount:
            self.queryset = self.queryset.filter(amount__gte=min_amount)
        if max_amount:
            self.queryset = self.queryset.filter(amount__lte=max_amount)

    def _apply_type_filters(self):
        """Filter by transaction_type, status, category, account."""
        tx_type = self.request.query_params.get("transaction_type")
        status = self.request.query_params.get("status")
        category = self.request.query_params.get("category")
        account = self.request.query_params.get("account")

        if tx_type:
            self.queryset = self.queryset.filter(transaction_type=tx_type)
        if status:
            self.queryset = self.queryset.filter(status=status)
        if category:
            self.queryset = self.queryset.filter(category_id=category)
        if account:
            self.queryset = self.queryset.filter(account_id=account)

    def _apply_ordering(self):
        """Order by transaction_date, amount, created_at."""
        ordering = self.request.query_params.get("ordering", "-transaction_date")
        allowed_fields = [
            "transaction_date",
            "-transaction_date",
            "amount",
            "-amount",
            "created_at",
            "-created_at",
        ]
        if ordering not in allowed_fields:
            ordering = "-transaction_date"
        self.queryset = self.queryset.order_by(ordering)


class BudgetFilter:
    """
    Advanced filtering for budgets.
    """

    def __init__(self, request: Request, queryset: models.QuerySet):
        self.request = request
        self.queryset = queryset

    def apply(self) -> models.QuerySet:
        self._apply_search()
        self._apply_basic_filters()
        self._apply_ordering()
        return self.queryset

    def _apply_basic_filters(self):
        budget_type = self.request.query_params.get("budget_type")
        is_active = self.request.query_params.get("is_active")

        if budget_type:
            self.queryset = self.queryset.filter(budget_type=budget_type)
        if is_active is not None:
            is_active = is_active.lower() == "true"
            self.queryset = self.queryset.filter(is_active=is_active)

    def _apply_search(self):
        search = self.request.query_params.get("search")
        if search:
            self.queryset = self.queryset.filter(name__icontains=search)

    def _apply_ordering(self):
        ordering = self.request.query_params.get("ordering", "-created_at")
        self.queryset = self.queryset.order_by(ordering)


class FinancialGoalFilter:
    """
    Advanced filtering for financial goals.
    """

    def __init__(self, request: Request, queryset: models.QuerySet):
        self.request = request
        self.queryset = queryset

    def apply(self) -> models.QuerySet:
        self._apply_search()
        self._apply_basic_filters()
        self._apply_ordering()
        return self.queryset

    def _apply_basic_filters(self):
        goal_type = self.request.query_params.get("goal_type")
        is_achieved = self.request.query_params.get("is_achieved")
        is_active = self.request.query_params.get("is_active")
        priority = self.request.query_params.get("priority")

        if goal_type:
            self.queryset = self.queryset.filter(goal_type=goal_type)
        if is_achieved is not None:
            is_achieved = is_achieved.lower() == "true"
            self.queryset = self.queryset.filter(is_achieved=is_achieved)
        if is_active is not None:
            is_active = is_active.lower() == "true"
            self.queryset = self.queryset.filter(is_active=is_active)
        if priority:
            self.queryset = self.queryset.filter(priority=priority)

    def _apply_search(self):
        search = self.request.query_params.get("search")
        if search:
            self.queryset = self.queryset.filter(
                Q(name__icontains=search) | Q(description__icontains=search)
            )

    def _apply_ordering(self):
        ordering = self.request.query_params.get("ordering", "-target_date")
        self.queryset = self.queryset.order_by(ordering)
