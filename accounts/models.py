import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _

from core.models import Category, Currency
from utils import choices, loggings

# Initialize logger
logger = loggings.setup_logging()


class Account(models.Model):
    """
    Account model representing a financial account (e.g., Bank, Cash, Mobile Money).
    """

    id = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False, unique=True
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="accounts",
        db_index=True,
    )
    name = models.CharField(
        max_length=255, help_text=_("Name of the account (e.g., 'Main Checking')")
    )
    account_type = models.CharField(
        max_length=50,
        choices=choices.AccountType.choices,
        default=choices.AccountType.CASH,
    )
    account_number = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text=_("Bank account number or identifier"),
    )
    bank_name = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text=_("Name of the bank or institution"),
    )

    # Currency linkage - strictly enforce valid currency from catalog
    currency = models.ForeignKey(
        Currency,
        on_delete=models.PROTECT,  # Prevent deleting currency if accounts use it
        related_name="accounts",
    )

    # Balances
    initial_balance = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    current_balance = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=_("Current calculated balance including all transactions"),
    )

    is_active = models.BooleanField(default=True, db_index=True)
    is_primary = models.BooleanField(
        default=False, help_text=_("Whether this is the user's primary account")
    )

    institution_data = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Store generic data for bank integrations"),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Account")
        verbose_name_plural = _("Accounts")
        ordering = ["-is_primary", "-created_at"]
        db_table = "accounts"
        indexes = [
            models.Index(fields=["user", "account_type"]),
        ]
        # Ensure name is unique per user to prevent duplicate confusion
        constraints = [
            models.UniqueConstraint(
                fields=["user", "name"], name="unique_account_name_per_user"
            )
        ]

    def __str__(self):
        """String representation: Name (Currency) - User."""
        return f"{self.name} ({self.currency.code}) - {self.user}"

    def clean(self):
        """
        Validate model invariants.
        """
        super().clean()
        if self.account_type == choices.AccountType.CASH and self.bank_name:
            # Not a critical error but logical check
            pass

    def save(self, *args, **kwargs):
        """
        Override save to handle 'is_primary' logic.
        If this account is set to primary, unset primary for all other user accounts.
        """
        if self.is_primary:
            try:
                with transaction.atomic():
                    # Unset primary for other accounts of this user
                    Account.objects.filter(user=self.user, is_primary=True).exclude(
                        id=self.id
                    ).update(is_primary=False)
            except Exception as e:
                logger.error(
                    f"Error updating primary account status for user {self.user.id}: {e}"
                )
                raise e

        super().save(*args, **kwargs)
        logger.debug(f"Account saved: {self.name} for user {self.user.id}")

    @property
    def available_balance(self):
        """
        Calculate available balance.
        For now, same as current_balance, but can be extended for pending transactions.
        """
        return self.current_balance


class Transaction(models.Model):
    """
    Represents a single financial transaction (income/expense/transfer).

    Key features:
    - Supports user, account, and category associations.
    - Handles currency conversion with original amount and exchange rate.
    - Supports recurring transactions and transfers.
    - Attachments and tags stored as JSON.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="transactions",
        db_index=True,
        help_text=_("Owner of the transaction"),
    )
    account = models.ForeignKey(
        Account,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
        db_index=True,
        help_text=_("Account associated with the transaction, optional"),
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="transactions",
        db_index=True,
        help_text=_("Category of the transaction"),
    )
    name = models.CharField(
        max_length=255, db_index=True, help_text=_("Transaction title or name")
    )
    transaction_type = models.CharField(
        max_length=10,
        choices=choices.TransactionType.choices,
        db_index=True,
        help_text=_("Income or Expense"),
    )
    amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        help_text=_("Transaction amount in account currency"),
    )
    original_amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_("Original amount before currency conversion"),
    )
    original_currency = models.CharField(
        max_length=3,
        null=True,
        blank=True,
        help_text=_("Original currency before conversion"),
    )
    exchange_rate = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        default=Decimal("1.0"),
        help_text=_("Exchange rate from original currency to account currency"),
    )
    currency = models.CharField(
        max_length=3,
        default="USD",
        db_index=True,
        help_text=_("Currency of the transaction"),
    )
    description = models.CharField(max_length=255, null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    status = models.CharField(
        max_length=50,
        choices=choices.TransactionStatus.choices,
        default=choices.TransactionStatus.PENDING,
        db_index=True,
    )
    tags = models.JSONField(default=list, blank=True)
    attachments = models.JSONField(default=list, blank=True)
    is_recurring = models.BooleanField(default=False)
    is_transfer = models.BooleanField(default=False)
    transaction_date = models.DateField(default=date.today, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Transaction"
        verbose_name_plural = "Transactions"
        ordering = ["-transaction_date", "-created_at"]
        indexes = [
            models.Index(fields=["user", "transaction_date"]),
            models.Index(fields=["account", "transaction_date"]),
            models.Index(fields=["category", "transaction_type"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_transaction_type_display()}) - {self.amount} {self.currency}"

    def clean(self):
        """
        Validate:
        - Amount must be positive
        - Exchange rate must be positive
        - Original amount and currency must be set if exchange_rate != 1
        """
        logger.debug(f"Validating transaction: {self.id} for user {self.user_id}")

        if self.amount <= 0:
            logger.warning("Transaction amount must be positive")
            raise ValidationError({"amount": _("Amount must be a positive value.")})

        if self.exchange_rate <= 0:
            logger.warning("Transaction exchange_rate must be positive")
            raise ValidationError(
                {"exchange_rate": _("Exchange rate must be positive.")}
            )

        if self.original_amount and self.original_amount <= 0:
            logger.warning("Original amount must be positive if set")
            raise ValidationError(
                {"original_amount": _("Original amount must be positive if provided.")}
            )

        if self.exchange_rate != 1 and (
            not self.original_amount or not self.original_currency
        ):
            logger.warning(
                "Original amount and currency must be provided for currency conversion"
            )
            raise ValidationError(
                {
                    "original_amount": _(
                        "Original amount and original currency must be set if exchange rate != 1."
                    )
                }
            )
