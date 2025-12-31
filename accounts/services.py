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
        accounts = Account.objects.filter(user=self.user, is_active=True)
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

        # 5. Safe to Spend Calculation
        # Definition: Liquid Cash - Upcoming Recurring (30d) - Pending Expenses
        upcoming_expenses_30d = RecurringTransaction.objects.filter(
            user=self.user,
            is_active=True,
            transaction_type=choices.TransactionType.EXPENSE,
            next_due_date__range=[today, today + timedelta(days=30)],
        ).aggregate(total=Sum("amount"))["total"] or Decimal(0)

        pending_expenses = Transaction.objects.filter(
            user=self.user,
            status=choices.TransactionStatus.PENDING,
            transaction_type=choices.TransactionType.EXPENSE,
        ).aggregate(total=Sum("amount"))["total"] or Decimal(0)

        liquid_cash = account_stats["cash_balance"] or Decimal(0)
        safe_to_spend = liquid_cash - upcoming_expenses_30d - pending_expenses

        return {
            "net_worth": {
                "total": (account_stats["total_assets"] or Decimal(0))
                + (account_stats["total_liabilities"] or Decimal(0)),
                "assets": account_stats["total_assets"] or Decimal(0),
                "liabilities": account_stats["total_liabilities"] or Decimal(0),
                "liquid_cash": liquid_cash,
            },
            "monthly_cash_flow": {
                "income": monthly_tx["income"] or Decimal(0),
                "expense": monthly_tx["expense"] or Decimal(0),
                "savings": (monthly_tx["income"] or Decimal(0))
                - (monthly_tx["expense"] or Decimal(0)),
            },
            "safe_to_spend": {
                "amount": safe_to_spend,
                "projected_expenses_30d": upcoming_expenses_30d,
                "pending_expenses": pending_expenses,
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
            is_active=True,
        ).aggregate(total=Sum("current_balance"))["total"] or Decimal(0)

        running_balance = current_balance

        # Get all active recurring transactions
        recurring = RecurringTransaction.objects.filter(user=self.user, is_active=True)

        # Simulate month by month or week by week for the response
        # We simulate day by day internally for accuracy
        end_date = today + timedelta(days=30 * months)
        cursor_date = today

        # Monthly snapshots
        while cursor_date <= end_date:
            daily_income = Decimal(0)
            daily_expense = Decimal(0)

            for item in recurring:
                if self._is_due_on_date(item, cursor_date):
                    if item.transaction_type == choices.TransactionType.INCOME:
                        daily_income += item.amount
                    else:
                        daily_expense += item.amount

            running_balance += daily_income - daily_expense

            # Snapshot on day 1 or last day
            if cursor_date.day == 1 or cursor_date == end_date:
                forecast.append(
                    {
                        "date": cursor_date,
                        "projected_balance": running_balance,
                    }
                )

            cursor_date += timedelta(days=1)

        return forecast

    def _is_due_on_date(self, item, target_date: date) -> bool:
        """Helper to check if a recurring item falls on a specific date."""
        if target_date < item.next_due_date:
            return False

        # Simplified projection logic
        # For a truly robust system, we would use rrule, but this is a good first step.
        if item.frequency == choices.FrequencyType.DAILY:
            return True
        elif item.frequency == choices.FrequencyType.WEEKLY:
            return (target_date - item.next_due_date).days % 7 == 0
        elif item.frequency == choices.FrequencyType.BI_WEEKLY:
            return (target_date - item.next_due_date).days % 14 == 0
        elif item.frequency == choices.FrequencyType.MONTHLY:
            # Check if day of month matches. Handle end-of-month (e.g. 31st vs 30th)
            if target_date.day == item.next_due_date.day:
                return True
            # Handle end of month if next_due_date was 31st but current month has only 30
            # This is simplified: Relativedelta is better.
            last_day_of_month = (
                target_date.replace(day=28) + timedelta(days=4)
            ).replace(day=1) - timedelta(days=1)
            if (
                target_date == last_day_of_month
                and item.next_due_date.day > target_date.day
            ):
                return True
        elif item.frequency == choices.FrequencyType.YEARLY:
            return (
                target_date.month == item.next_due_date.month
                and target_date.day == item.next_due_date.day
            )

        return False
