from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, Any, List

from django.db.models import Sum, Q, Count
from django.utils import timezone
from django.db import transaction as db_transaction

from .models import Account, Transaction, Budget, FinancialGoal, RecurringTransaction
from utils import choices, loggings

logger = loggings.setup_logging()


class AnalyticsService:
    """
    Service layer for financial analytics, dashboards, and forecasting.
    """

    def __init__(self, user):
        self.user = user

    def get_dashboard_data(self) -> Dict[str, Any]:
        """
        Get aggregated data for the main dashboard.
        Optimized for single-query performance where possible.
        """
        today = timezone.now().date()
        month_start = today.replace(day=1)

        # 1. Net Worth (Assets - Liabilities)
        # Using a single aggregate query if possible or two efficient ones
        accounts = Account.objects.filter(user=self.user, is_active=True)
        # We can fetch everything we need about accounts in one go
        account_stats = accounts.aggregate(
            total_assets=Sum("current_balance", filter=Q(current_balance__gt=0)),
            total_liabilities=Sum("current_balance", filter=Q(current_balance__lt=0)),
            cash_balance=Sum(
                "current_balance",
                filter=Q(
                    account_type__in=[
                        choices.AccountType.CASH,
                        choices.AccountType.CHECKING,
                        choices.AccountType.SAVINGS,
                    ]
                ),
            ),
        )

        # 2. Monthly Cash Flow (Income vs Expense)
        monthly_tx = Transaction.objects.filter(
            user=self.user,
            transaction_date__gte=month_start,
            status__in=[
                choices.TransactionStatus.COMPLETED,
                choices.TransactionStatus.RECONCILED,
            ],
        ).aggregate(
            income=Sum(
                "amount", filter=Q(transaction_type=choices.TransactionType.INCOME)
            ),
            expense=Sum(
                "amount", filter=Q(transaction_type=choices.TransactionType.EXPENSE)
            ),
        )

        # 3. Upcoming Recurring Bills (Next 7 days)
        upcoming_bills = RecurringTransaction.objects.filter(
            user=self.user,
            is_active=True,
            transaction_type=choices.TransactionType.EXPENSE,
            next_due_date__range=[today, today + timedelta(days=7)],
        ).order_by("next_due_date")[:5]

        # 4. Top Budget Categories (Utilization)
        # Fetch active budgets for this month
        budgets = Budget.objects.filter(
            user=self.user, start_date__lte=today, end_date__gte=today, is_active=True
        ).select_related("category")

        budget_alerts = []
        for b in budgets:
            utilization = b.get_utilization_percentage()
            if utilization >= 80:
                budget_alerts.append(
                    {
                        "category": b.category.name if b.category else "Overall",
                        "utilization": utilization,
                        "remaining": b.total_remaining,
                    }
                )

        return {
            "net_worth": {
                "total": (account_stats["total_assets"] or 0)
                + (account_stats["total_liabilities"] or 0),
                "assets": account_stats["total_assets"] or 0,
                "liabilities": account_stats["total_liabilities"] or 0,
                "liquid_cash": account_stats["cash_balance"] or 0,
            },
            "monthly_cash_flow": {
                "income": monthly_tx["income"] or 0,
                "expense": monthly_tx["expense"] or 0,
                "savings": (monthly_tx["income"] or 0) - (monthly_tx["expense"] or 0),
            },
            "upcoming_bills": [
                {
                    "name": bill.name,
                    "amount": bill.amount,
                    "due_date": bill.next_due_date,
                    "days_until": (bill.next_due_date - today).days,
                }
                for bill in upcoming_bills
            ],
            "budget_health": {
                "status": "Healthy" if not budget_alerts else "Warning",
                "alerts": sorted(
                    budget_alerts, key=lambda x: x["utilization"], reverse=True
                )[:3],
            },
        }

    def get_forecast(self, months: int = 3) -> List[Dict[str, Any]]:
        """
        Forecast account balances for upcoming months based on recurring transactions.
        """
        today = timezone.now().date()
        forecast = []

        # Get current flexible balance (Cash + Checking)
        current_balance = Account.objects.filter(
            user=self.user,
            account_type__in=[choices.AccountType.CHECKING, choices.AccountType.CASH],
        ).aggregate(total=Sum("current_balance"))["total"] or Decimal(0)

        running_balance = current_balance

        # Simulate timeline
        end_date = today + timedelta(days=30 * months)

        # Get all active recurring transactions
        recurring = RecurringTransaction.objects.filter(user=self.user, is_active=True)

        # Simple daily simulation
        # Note: In production, this might be optimized by grouping by frequency
        # but iterating days is robust for calendar accuracy

        cursor_date = today
        while cursor_date <= end_date:
            daily_income = Decimal(0)
            daily_expense = Decimal(0)

            # Check for due recurring items
            # Optimization: This loop inside loop is O(N*M), for N days M recurring items.
            # M is usually small (<50), N is ~90. 4500 iterations is trivial in Python.
            for item in recurring:
                # We need a stateless way to check if item is due on cursor_date
                # Assuming item.next_due_date is the anchor.
                # We need logic to project 'is due on date' without modifying DB
                if self._is_due_on_date(item, cursor_date):
                    if item.transaction_type == choices.TransactionType.INCOME:
                        daily_income += item.amount
                    else:
                        daily_expense += item.amount

            net_change = daily_income - daily_expense
            running_balance += net_change

            # Record snapshot every week or month end for the graph
            if cursor_date.day == 1 or cursor_date == end_date:
                forecast.append(
                    {
                        "date": cursor_date,
                        "projected_balance": running_balance,
                        "projected_income": daily_income,  # granular daily isn't helpful for monthly graph, maybe aggregate?
                    }
                )

            cursor_date += timedelta(days=1)

        return forecast

    def _is_due_on_date(self, item, target_date: date) -> bool:
        """Helper to check if a recurring item falls on a specific date."""
        # Simple logic: if target_date matches the item's frequency pattern starting from start_date
        # or easier: check if target_date matches next_due_date logic.
        # However, next_due_date changes.
        # Better approach: project 'next dates' from the DB's next_due_date

        if target_date < item.next_due_date:
            return False

        if item.frequency == choices.FrequencyType.MONTHLY:
            # Check if same day of month
            # Handle end of month logic if needed
            if target_date.day == item.next_due_date.day:
                return True
        elif item.frequency == choices.FrequencyType.WEEKLY:
            days_diff = (target_date - item.next_due_date).days
            if days_diff % 7 == 0:
                return True
        elif item.frequency == choices.FrequencyType.DAILY:
            return True

        return False
