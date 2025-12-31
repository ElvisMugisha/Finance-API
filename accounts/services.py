from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List

from django.db.models import Q, Sum
from django.utils import timezone

from core.models import Currency
from utils import choices, loggings

from .models import (
    Account,
    Budget,
    RecurringTransaction,
    Transaction,
)

logger = loggings.setup_logging()


class AnalyticsService:
    """
    Service layer for financial analytics, dashboards, and forecasting.
    """

    def __init__(self, user):
        self.user = user
        self.base_currency = Currency.get_base_currency()

    def _convert_to_base(self, amount: Decimal, source_currency: Currency) -> Decimal:
        """Helper to convert amount to system base currency."""
        if not self.base_currency or source_currency == self.base_currency:
            return amount

        converted = source_currency.convert_amount(amount, self.base_currency)
        return converted if converted is not None else amount

    def get_dashboard_data(self) -> Dict[str, Any]:
        """
        Get aggregated data for the main dashboard.
        Optimized for single-query performance where possible.
        """
        today = timezone.now().date()
        month_start = today.replace(day=1)

        # 1. Net Worth (Assets - Liabilities)
        accounts = Account.objects.filter(
            user=self.user, is_active=True
        ).select_related("currency")

        total_assets = Decimal("0.00")
        total_liabilities = Decimal("0.00")
        liquid_cash = Decimal("0.00")

        liquid_types = [
            choices.AccountType.CASH,
            choices.AccountType.CHECKING,
            choices.AccountType.SAVINGS,
        ]

        for acc in accounts:
            balance_in_base = self._convert_to_base(acc.current_balance, acc.currency)
            if balance_in_base > 0:
                total_assets += balance_in_base
            else:
                total_liabilities += balance_in_base

            if acc.account_type in liquid_types:
                liquid_cash += balance_in_base

        # 2. Monthly Cash Flow (Income vs Expense)
        # We group by account currency to handle conversions efficiently
        monthly_tx_groups = (
            Transaction.objects.filter(
                user=self.user,
                transaction_date__gte=month_start,
                status__in=[
                    choices.TransactionStatus.COMPLETED,
                    choices.TransactionStatus.RECONCILED,
                ],
            )
            .values("account__currency")
            .annotate(
                income=Sum(
                    "amount", filter=Q(transaction_type=choices.TransactionType.INCOME)
                ),
                expense=Sum(
                    "amount", filter=Q(transaction_type=choices.TransactionType.EXPENSE)
                ),
            )
        )

        total_income = Decimal("0.00")
        total_expense = Decimal("0.00")

        for group in monthly_tx_groups:
            currency_id = group["account__currency"]
            if not currency_id:
                continue

            try:
                currency = Currency.objects.get(id=currency_id)
                total_income += self._convert_to_base(
                    group["income"] or Decimal(0), currency
                )
                total_expense += self._convert_to_base(
                    group["expense"] or Decimal(0), currency
                )
            except Currency.DoesNotExist:
                continue

        # 3. Upcoming Recurring Bills (Next 7 days)
        upcoming_bills = (
            RecurringTransaction.objects.filter(
                user=self.user,
                is_active=True,
                transaction_type=choices.TransactionType.EXPENSE,
                next_due_date__range=[today, today + timedelta(days=7)],
            )
            .select_related("currency")
            .order_by("next_due_date")[:5]
        )

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
        recurring_30d = RecurringTransaction.objects.filter(
            user=self.user,
            is_active=True,
            transaction_type=choices.TransactionType.EXPENSE,
            next_due_date__range=[today, today + timedelta(days=30)],
        ).select_related("currency")

        upcoming_expenses_30d = Decimal("0.00")
        for r in recurring_30d:
            upcoming_expenses_30d += self._convert_to_base(r.amount, r.currency)

        pending_tx = Transaction.objects.filter(
            user=self.user,
            status=choices.TransactionStatus.PENDING,
            transaction_type=choices.TransactionType.EXPENSE,
        ).select_related("account__currency")

        pending_expenses = Decimal("0.00")
        for tx in pending_tx:
            if tx.account:
                pending_expenses += self._convert_to_base(
                    tx.amount, tx.account.currency
                )

        safe_to_spend = liquid_cash - upcoming_expenses_30d - pending_expenses

        return {
            "net_worth": {
                "total": total_assets + total_liabilities,
                "assets": total_assets,
                "liabilities": total_liabilities,
                "liquid_cash": liquid_cash,
                "currency": self.base_currency.code if self.base_currency else "USD",
            },
            "monthly_cash_flow": {
                "income": total_income,
                "expense": total_expense,
                "savings": total_income - total_expense,
                "currency": self.base_currency.code if self.base_currency else "USD",
            },
            "safe_to_spend": {
                "amount": safe_to_spend,
                "projected_expenses_30d": upcoming_expenses_30d,
                "pending_expenses": pending_expenses,
                "currency": self.base_currency.code if self.base_currency else "USD",
            },
            "upcoming_bills": [
                {
                    "name": bill.name,
                    "amount": bill.amount,
                    "currency": bill.currency.code,
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
        All calculations are normalized to the system's base currency.
        """
        today = timezone.now().date()
        forecast = []

        # Get current flexible balance (Cash + Checking + Savings) in base currency
        liquid_types = [
            choices.AccountType.CASH,
            choices.AccountType.CHECKING,
            choices.AccountType.SAVINGS,
        ]
        accounts = Account.objects.filter(
            user=self.user,
            account_type__in=liquid_types,
            is_active=True,
        ).select_related("currency")

        current_balance_base = Decimal("0.00")
        for acc in accounts:
            current_balance_base += self._convert_to_base(
                acc.current_balance, acc.currency
            )

        running_balance = current_balance_base

        # Get all active recurring transactions with their currencies
        recurring = RecurringTransaction.objects.filter(
            user=self.user, is_active=True
        ).select_related("currency")

        # Simulate month by month or week by week for the response
        end_date = today + timedelta(days=30 * months)
        cursor_date = today

        # Monthly snapshots
        while cursor_date <= end_date:
            daily_income = Decimal("0.00")
            daily_expense = Decimal("0.00")

            for item in recurring:
                if self._is_due_on_date(item, cursor_date):
                    amount_in_base = self._convert_to_base(item.amount, item.currency)
                    if item.transaction_type == choices.TransactionType.INCOME:
                        daily_income += amount_in_base
                    else:
                        daily_expense += amount_in_base

            running_balance += daily_income - daily_expense

            # Snapshot on day 1 (start of month) or last day of simulation
            if cursor_date.day == 1 or cursor_date == end_date:
                forecast.append(
                    {
                        "date": cursor_date,
                        "projected_balance": running_balance,
                        "currency": (
                            self.base_currency.code if self.base_currency else "USD"
                        ),
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
