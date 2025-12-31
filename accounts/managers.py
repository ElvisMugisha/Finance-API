import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional

from django.db import models
from django.db.models import Case, Q, Sum, Value, When
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
        """
        Get summary of all user accounts.
        Normalizes total balance to system base currency.
        """
        from core.models import Currency

        try:
            accounts = self.filter(user_id=user_id, is_active=True).select_related(
                "currency"
            )
            base_currency = Currency.get_base_currency()

            total_balance_base = Decimal("0.00")
            currency_balances = {}

            for account in accounts:
                # Normalize for the main total
                if base_currency:
                    balance_in_base = account.currency.convert_amount(
                        account.current_balance, base_currency
                    )
                    if balance_in_base is not None:
                        total_balance_base += balance_in_base
                else:
                    # Fallback if no base currency defined (not ideal)
                    total_balance_base += account.current_balance

                currency_code = account.currency.code
                if currency_code not in currency_balances:
                    currency_balances[currency_code] = {
                        "balance": Decimal("0.00"),
                        "currency_id": str(account.currency.id),
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
                "total_balance_base": total_balance_base,
                "base_currency": base_currency.code if base_currency else "USD",
                "currency_balances": currency_balances,
                "account_count": accounts.count(),
                "primary_account": self.get_user_primary_account(user_id),
            }

        except Exception as e:
            logger.exception(f"Error getting accounts summary for user {user_id}: {e}")
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
        """
        Get transaction summary for a user.
        Note: Simple sum of 'amount' may be multi-currency inconsistent if
        accounts have different currencies.
        """
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
                            output_field=models.DecimalField(
                                max_digits=18, decimal_places=2
                            ),
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
                            output_field=models.DecimalField(
                                max_digits=18, decimal_places=2
                            ),
                        )
                    ),
                    Decimal("0.00"),
                ),
            )

            summary["net_flow"] = summary["total_income"] - summary["total_expense"]
            return summary

        except Exception as e:
            logger.exception(
                f"Error getting transaction summary for user {user_id}: {e}"
            )
            return {}

    def aggregate_to_currency(self, queryset, target_currency) -> Decimal:
        """
        Calculate the sum of transaction amounts in a queryset,
        normalized to a single target currency.

        Args:
            queryset: Transaction QuerySet
            target_currency: Currency instance to convert to

        Returns:
            Decimal: Total sum in target currency
        """
        from core.models import Currency

        # Group by account currency to minimize conversion work
        currency_groups = queryset.values("account__currency").annotate(
            total=Coalesce(Sum("amount"), Decimal("0.00"))
        )

        total_converted = Decimal("0.00")

        # Fetch involved currencies to avoid N+1
        currency_ids = [
            g["account__currency"] for g in currency_groups if g["account__currency"]
        ]
        if not currency_ids:
            return total_converted

        currencies = {
            str(c.id): c for c in Currency.objects.filter(id__in=currency_ids)
        }

        for group in currency_groups:
            currency_id = group["account__currency"]
            if not currency_id:
                continue

            amount = group["total"]
            if str(currency_id) == str(target_currency.id):
                total_converted += amount
            else:
                source_currency = currencies.get(str(currency_id))
                if source_currency:
                    converted = source_currency.convert_amount(amount, target_currency)
                    if converted is not None:
                        total_converted += converted

        return total_converted

    def aggregate_by_category_to_currency(
        self, queryset, target_currency
    ) -> List[Dict[str, Any]]:
        """
        Aggregate amounts from a transaction queryset grouped by category,
        normalizing all amounts to target currency.
        """
        from core.models import Currency

        # Group by category name and account currency
        groups = (
            queryset.values("category__name", "account__currency")
            .annotate(total=Coalesce(Sum("amount"), Decimal("0.00")))
            .order_by("category__name")
        )

        # Fetch currencies to avoid N+1
        currency_ids = {
            g["account__currency"] for g in groups if g["account__currency"]
        }
        if not currency_ids:
            return []

        currencies = {
            str(c.id): c for c in Currency.objects.filter(id__in=list(currency_ids))
        }

        category_totals = {}

        for group in groups:
            cat_name = group["category__name"] or "Uncategorized"
            currency_id = group["account__currency"]
            amount = group["total"]

            if not currency_id:
                continue

            if cat_name not in category_totals:
                category_totals[cat_name] = Decimal("0.00")

            if str(currency_id) == str(target_currency.id):
                category_totals[cat_name] += amount
            else:
                source_currency = currencies.get(str(currency_id))
                if source_currency:
                    converted = source_currency.convert_amount(amount, target_currency)
                    if converted is not None:
                        category_totals[cat_name] += converted

        # Format and sort
        result = [
            {"category": name, "amount": total}
            for name, total in category_totals.items()
        ]
        return sorted(result, key=lambda x: x["amount"], reverse=True)


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
