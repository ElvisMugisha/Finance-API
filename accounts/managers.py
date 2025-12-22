import uuid
from decimal import Decimal
from typing import Any, Dict, Optional

from django.db import models
from django.db.models import Case, Sum, Value, When, Q
from django.db.models.functions import Coalesce
from django.utils import timezone

from utils import choices, loggings

# Initialize logger
logger = loggings.setup_logging()


class AccountManager(models.Manager):
    """
    Manager for Account model with helper methods.
    """

    def active(self):
        """Return only active accounts."""
        return self.filter(is_active=True)

    def primary(self):
        """Return primary accounts."""
        return self.filter(is_primary=True, is_active=True)

    def get_user_primary_account(self, user_id: uuid.UUID) -> Optional[Any]:
        """Get user's primary account."""
        return self.filter(user_id=user_id, is_primary=True, is_active=True).first()

    def get_user_accounts_summary(self, user_id: uuid.UUID) -> Dict[str, Any]:
        """Get summary of all user accounts."""
        try:
            accounts = self.filter(user_id=user_id, is_active=True).select_related(
                "currency"
            )

            total_balance = Decimal("0.00")
            currency_balances = {}

            for account in accounts:
                total_balance += account.current_balance
                currency_code = account.currency.code
                if currency_code not in currency_balances:
                    currency_balances[currency_code] = {
                        "balance": Decimal("0.00"),
                        "currency": account.currency,
                        "accounts": [],
                    }
                currency_balances[currency_code]["balance"] += account.current_balance
                currency_balances[currency_code]["accounts"].append(
                    {
                        "id": str(account.id),
                        "name": account.name,
                        "balance": account.current_balance,
                        "type": account.account_type,
                    }
                )

            return {
                "total_balance": total_balance,
                "currency_balances": currency_balances,
                "account_count": accounts.count(),
                "primary_account": self.get_user_primary_account(user_id),
            }

        except Exception as e:
            logger.error(f"Error getting accounts summary for user {user_id}: {e}")
            return {}


class TransactionManager(models.Manager):
    """
    Manager for Transaction model with helper methods.
    """

    def completed(self):
        """Return only completed transactions."""
        return self.filter(
            status__in=[
                choices.TransactionStatus.COMPLETED,
                choices.TransactionStatus.RECONCILED,
            ]
        )

    def pending(self):
        """Return pending transactions."""
        return self.filter(status=choices.TransactionStatus.PENDING)

    def get_user_transactions_summary(
        self,
        user_id: uuid.UUID,
        start_date: Optional[timezone.datetime.date] = None,
        end_date: Optional[timezone.datetime.date] = None,
    ) -> Dict[str, Any]:
        """Get transaction summary for a user."""
        try:
            query = Q(user_id=user_id)
            if start_date:
                query &= Q(transaction_date__gte=start_date)
            if end_date:
                query &= Q(transaction_date__lte=end_date)

            transactions = self.filter(query)

            summary = transactions.aggregate(
                total_count=models.Count("id"),
                total_income=Coalesce(
                    Sum(
                        Case(
                            When(
                                transaction_type=choices.TransactionType.INCOME,
                                then="amount",
                            ),
                            default=Value(0),
                            output_field=models.DecimalField(),
                        )
                    ),
                    Decimal("0.00"),
                ),
                total_expense=Coalesce(
                    Sum(
                        Case(
                            When(
                                transaction_type=choices.TransactionType.EXPENSE,
                                then="amount",
                            ),
                            default=Value(0),
                            output_field=models.DecimalField(),
                        )
                    ),
                    Decimal("0.00"),
                ),
            )

            summary["net_flow"] = summary["total_income"] - summary["total_expense"]
            return summary

        except Exception as e:
            logger.error(f"Error getting transaction summary for user {user_id}: {e}")
            return {}


class BudgetManager(models.Manager):
    """
    Manager for Budget model with helper methods.
    """

    def active(self):
        """Return only active budgets."""
        return self.filter(is_active=True)


class FinancialGoalManager(models.Manager):
    """
    Manager for FinancialGoal model with helper methods.
    """

    def active(self):
        """Return only active goals."""
        return self.filter(is_active=True)


class ReportManager(models.Manager):
    """
    Manager for Report model with helper methods.
    """

    def completed(self):
        """Return only completed reports."""
        return self.filter(status=choices.ReportStatus.COMPLETED)
