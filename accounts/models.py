import hashlib
import json
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, Optional

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import DatabaseError, models, transaction
from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import Category, Currency
from utils import choices, utils, loggings, models as utils_models

# Initialize logger
logger = loggings.setup_logging()


class Account(utils_models.BaseModel):
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
        help_text=_("Owner of the account"),
    )
    name = models.CharField(
        max_length=255,
        db_index=True,
        help_text=_("Account name (e.g., 'Main Checking', 'Savings')"),
    )
    account_type = models.CharField(
        max_length=50,
        choices=choices.AccountType.choices,
        default=choices.AccountType.CASH,
        db_index=True,
        help_text=_("Type of account"),
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
    bank_code = models.CharField(
        max_length=50, null=True, blank=True, help_text=_("Bank code or routing number")
    )

    # Currency linkage - strictly enforce valid currency from catalog
    currency = models.ForeignKey(
        Currency,
        on_delete=models.PROTECT,  # Prevent deleting currency if accounts use it
        related_name="accounts",
        help_text=_("Account currency"),
    )

    # Balances
    initial_balance = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=_("Starting balance when account was created"),
    )
    current_balance = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=_("Current calculated balance including all transactions"),
    )
    reconciled_balance = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_("Last reconciled balance"),
    )
    reconciled_at = models.DateTimeField(
        null=True, blank=True, help_text=_("When the account was last reconciled")
    )

    is_active = models.BooleanField(
        default=True, db_index=True, help_text=_("Whether this account is active")
    )
    is_primary = models.BooleanField(
        default=False,
        db_index=True,
        help_text=_("Whether this is the user's primary account"),
    )
    is_locked = models.BooleanField(
        default=False,
        help_text=_("Whether this account is locked (no transactions allowed)"),
    )

    institution_data = models.JSONField(
        default=dict, blank=True, help_text=_("Additional data for bank integrations")
    )
    last_synced_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When the account was last synced with external service"),
    )
    # Audit trail for balance changes
    balance_updated_at = models.DateTimeField(
        null=True, blank=True, help_text=_("When the current balance was last updated")
    )

    class Meta:
        verbose_name = _("Account")
        verbose_name_plural = _("Accounts")
        ordering = ["-is_primary", "-created_at"]
        db_table = "accounts"
        indexes = [
            models.Index(fields=["user", "account_type"]),
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["user", "currency"]),
            models.Index(fields=["user", "is_primary"]),
            # Composite index for common account queries
            models.Index(
                fields=["user", "is_active", "account_type"],
                name=utils.get_index_name(
                    "accounts", ["user", "is_active", "account_type"]
                ),
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "name"],
                name=utils.get_index_name("accounts", ["user", "name"]),
            ),
            models.CheckConstraint(
                condition=models.Q(
                    current_balance__gte=Decimal("-1000000000")
                ),  # Reasonable minimum
                name=utils.get_index_name("accounts", ["current_balance"]),
            ),
        ]

    def __str__(self) -> str:
        """Human-readable representation."""
        primary = "★ " if self.is_primary else ""
        return f"{primary}{self.name} ({self.currency.code}) - {self.user}"

    def clean(self) -> None:
        """
        Validate account fields and business rules.

        Raises:
            ValidationError: If validation fails
        """
        logger.debug(f"Validating account: {self.name}")

        # Account number validation based on type
        if self.account_type == choices.AccountType.BANK and not self.account_number:
            logger.warning(f"Bank account missing account number: {self.name}")

        # Balance validation
        if self.current_balance < Decimal("-1000000"):  # Arbitrary large negative
            raise ValidationError(
                {"current_balance": _("Balance cannot be less than -1,000,000.")}
            )

        # Institution validation
        if self.account_type in [choices.AccountType.CASH, choices.AccountType.WALLET]:
            if self.bank_name or self.account_number:
                logger.info(
                    f"Clearing institution details for {self.account_type} account: {self.name}"
                )
                self.bank_name = None
                self.account_number = None

        logger.debug(f"Account validation passed: {self.name}")

    def save(self, *args, **kwargs):
        """
        Override save to handle primary account logic and balance updates.
        """
        # Handle primary account logic
        if self.is_primary and self.is_active:
            try:
                with transaction.atomic():
                    # Unset primary for other accounts
                    Account.objects.filter(user=self.user, is_primary=True).exclude(
                        id=self.id
                    ).update(is_primary=False)

                    logger.debug(f"Set account as primary: {self.name}")
            except Exception as e:
                logger.error(f"Error setting primary account: {e}")
                raise

        # Update balance timestamp
        if "current_balance" in self.get_deferred_fields():
            self.balance_updated_at = timezone.now()

        super().save(*args, **kwargs)

    def update_balance(self, amount: Decimal, transaction_type: str) -> bool:
        """
        Update account balance based on transaction.

        Args:
            amount: Transaction amount (positive)
            transaction_type: 'income' or 'expense'

        Returns:
            True if balance updated successfully
        """
        if not isinstance(amount, Decimal):
            try:
                amount = Decimal(str(amount))
            except (InvalidOperation, TypeError):
                logger.error(f"Invalid amount for balance update: {amount}")
                return False

        if amount <= 0:
            logger.error(f"Amount must be positive for balance update: {amount}")
            return False

        try:
            with transaction.atomic():
                if transaction_type == choices.TransactionType.INCOME:
                    self.current_balance += amount
                elif transaction_type == choices.TransactionType.EXPENSE:
                    self.current_balance -= amount
                else:
                    logger.error(
                        f"Invalid transaction type for balance update: {transaction_type}"
                    )
                    return False

                self.balance_updated_at = timezone.now()
                self.save(
                    update_fields=[
                        "current_balance",
                        "balance_updated_at",
                        "updated_at",
                    ]
                )

                logger.debug(
                    f"Updated balance for account {self.id}: {self.current_balance}"
                )
                return True

        except Exception as e:
            logger.error(f"Error updating balance for account {self.id}: {e}")
            return False

    @property
    def available_balance(self) -> Decimal:
        """
        Calculate available balance (current balance minus any holds).

        For now, same as current_balance. Can be extended for pending transactions.
        """
        return self.current_balance

    @property
    def formatted_balance(self) -> str:
        """Get formatted balance with currency symbol."""
        symbol = self.currency.symbol or self.currency.code
        return f"{symbol}{self.current_balance:.2f}"

    def get_balance_history(
        self, start_date: Optional[date] = None, end_date: Optional[date] = None
    ) -> List[Dict[str, Any]]:
        """
        Get balance history within date range.

        Args:
            start_date: Start date (default: 30 days ago)
            end_date: End date (default: today)

        Returns:
            List of balance snapshots
        """
        from .models import Transaction

        if start_date is None:
            start_date = timezone.now().date() - timedelta(days=30)
        if end_date is None:
            end_date = timezone.now().date()

        try:
            # Get daily balances from transactions
            transactions = (
                Transaction.objects.filter(
                    account=self,
                    transaction_date__gte=start_date,
                    transaction_date__lte=end_date,
                    status=choices.TransactionStatus.COMPLETED,
                )
                .values("transaction_date")
                .annotate(
                    day=models.F("transaction_date"),
                    income=Coalesce(
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
                    expense=Coalesce(
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
                .order_by("day")
            )

            # Build balance history
            balance = self.initial_balance
            history = []

            for tx in transactions:
                balance += tx["income"] - tx["expense"]
                history.append(
                    {
                        "date": tx["day"],
                        "balance": balance,
                        "income": tx["income"],
                        "expense": tx["expense"],
                    }
                )

            return history

        except Exception as e:
            logger.error(f"Error getting balance history for account {self.id}: {e}")
            return []

    @classmethod
    def get_user_primary_account(cls, user_id: uuid.UUID) -> Optional["Account"]:
        """Get user's primary account."""
        return cls.objects.filter(
            user_id=user_id, is_primary=True, is_active=True
        ).first()

    @classmethod
    def get_user_accounts_summary(cls, user_id: uuid.UUID) -> Dict[str, Any]:
        """Get summary of all user accounts."""
        try:
            accounts = cls.objects.filter(user_id=user_id, is_active=True)

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
                "primary_account": cls.get_user_primary_account(user_id),
            }

        except Exception as e:
            logger.error(f"Error getting accounts summary for user {user_id}: {e}")
            return {}


class Transaction(utils_models.BaseModel):
    """
    Core transaction model with comprehensive financial tracking.

    Features:
    - Multi-currency support with automatic conversion
    - Category and account associations
    - Transfer tracking
    - Recurring transaction linking
    - Comprehensive status tracking
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Ownership
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="transactions",
        db_index=True,
        help_text=_("Owner of the transaction"),
    )

    account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,  # Changed from SET_NULL for data integrity
        related_name="transactions",
        null=True,
        blank=True,
        db_index=True,
        help_text=_("Account associated with the transaction"),
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="transactions",
        db_index=True,
        help_text=_("Transaction category"),
    )

    # Recurring transaction link (if applicable)
    # recurring_transaction = models.ForeignKey(
    #     "RecurringTransaction",
    #     on_delete=models.SET_NULL,
    #     null=True,
    #     blank=True,
    #     related_name="generated_transactions",
    #     help_text=_("Recurring transaction that generated this transaction"),
    # )
    recurrence_metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Basic recurrence configuration (frequency, interval, etc.)"),
    )

    # Transfer tracking (for transfers between accounts)
    transfer_account = models.ForeignKey(
        Account,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transfer_transactions",
        help_text=_("Destination account for transfers"),
    )
    transfer_reference = models.UUIDField(
        null=True,
        blank=True,
        help_text=_("Reference to paired transaction in transfer"),
    )

    # Transaction details
    name = models.CharField(
        max_length=255, db_index=True, help_text=_("Transaction description or name")
    )
    transaction_type = models.CharField(
        max_length=10,
        choices=choices.TransactionType.choices,
        db_index=True,
        help_text=_("Income or expense"),
    )

    # Amounts and currency
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
    original_currency = models.ForeignKey(
        Currency,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="original_transactions",
        help_text=_("Original currency before conversion"),
    )
    exchange_rate = models.DecimalField(
        max_digits=18,
        decimal_places=8,
        default=Decimal("1.0"),
        help_text=_("Exchange rate from original to account currency"),
    )

    # Dates
    transaction_date = models.DateField(
        default=date.today, db_index=True, help_text=_("Date when transaction occurred")
    )
    posted_date = models.DateField(
        null=True, blank=True, help_text=_("Date when transaction was posted by bank")
    )

    # Status
    status = models.CharField(
        max_length=50,
        choices=choices.TransactionStatus.choices,
        default=choices.TransactionStatus.PENDING,
        db_index=True,
        help_text=_("Transaction status"),
    )

    # Metadata
    description = models.TextField(
        null=True, blank=True, help_text=_("Detailed description")
    )
    merchant = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        db_index=True,
        help_text=_("Merchant or payee name"),
    )
    location = models.CharField(
        max_length=500, null=True, blank=True, help_text=_("Transaction location")
    )
    reference_number = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        db_index=True,
        help_text=_("Bank or payment reference number"),
    )

    # Tags and attachments
    tags = models.JSONField(
        default=list, blank=True, help_text=_("Tags for categorization and filtering")
    )
    attachments = models.JSONField(
        default=list, blank=True, help_text=_("Metadata for attached files")
    )

    # Flags
    is_recurring = models.BooleanField(
        default=False, help_text=_("Whether this is part of a recurring transaction")
    )
    is_transfer = models.BooleanField(
        default=False,
        db_index=True,
        help_text=_("Whether this is a transfer between accounts"),
    )
    needs_review = models.BooleanField(
        default=False,
        db_index=True,
        help_text=_("Whether this transaction needs manual review"),
    )
    is_tax_deductible = models.BooleanField(
        default=False, help_text=_("Whether this expense is tax deductible")
    )

    # Audit fields
    imported_at = models.DateTimeField(
        null=True, blank=True, help_text=_("When this transaction was imported")
    )
    imported_source = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text=_("Source of import (e.g., 'CSV', 'Bank API')"),
    )

    class Meta:
        verbose_name = _("Transaction")
        verbose_name_plural = _("Transactions")
        ordering = ["-transaction_date", "-created_at"]
        db_table = "transactions"
        indexes = [
            # Common filtering patterns
            models.Index(fields=["user", "transaction_date"]),
            models.Index(fields=["account", "transaction_date"]),
            models.Index(fields=["category", "transaction_date"]),
            models.Index(fields=["status", "transaction_date"]),
            models.Index(fields=["transaction_type", "transaction_date"]),
            # Composite indexes for performance
            models.Index(
                fields=["user", "status", "transaction_date"],
                name=utils.get_index_name(
                    "transactions", ["user", "status", "transaction_date"]
                ),
            ),
            models.Index(
                fields=["account", "status", "transaction_date"],
                name=utils.get_index_name(
                    "transactions", ["account", "status", "transaction_date"]
                ),
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name=utils.get_index_name("transactions", ["amount"]),
            ),
            models.CheckConstraint(
                condition=models.Q(exchange_rate__gt=0),
                name=utils.get_index_name("transactions", ["exchange_rate"]),
            ),
        ]

    def __str__(self) -> str:
        """Human-readable representation."""
        return f"{self.name} - {self.amount} {self.account.currency.code}"

    def clean(self) -> None:
        """
        Validate transaction fields and business rules.

        Raises:
            ValidationError: If validation fails
        """
        logger.debug(f"Validating transaction: {self.id}")

        # Amount validation
        if self.amount <= 0:
            raise ValidationError({"amount": _("Amount must be positive.")})

        # Exchange rate validation
        if self.exchange_rate <= 0:
            raise ValidationError(
                {"exchange_rate": _("Exchange rate must be positive.")}
            )

        # Currency conversion validation
        if self.exchange_rate != Decimal("1.0"):
            if not self.original_amount or not self.original_currency:
                raise ValidationError(
                    {
                        "original_amount": _(
                            "Original amount and currency required when exchange rate != 1."
                        ),
                        "original_currency": _(
                            "Original amount and currency required when exchange rate != 1."
                        ),
                    }
                )

            if self.original_amount <= 0:
                raise ValidationError(
                    {"original_amount": _("Original amount must be positive.")}
                )

        # Transfer validation
        if self.is_transfer:
            if not self.transfer_account:
                raise ValidationError(
                    {"transfer_account": _("Transfer account required for transfers.")}
                )

            if self.transfer_account.id == self.account.id:
                raise ValidationError(
                    {"transfer_account": _("Cannot transfer to the same account.")}
                )

        # Date validation
        if self.posted_date and self.posted_date < self.transaction_date:
            logger.warning(
                f"Posted date earlier than transaction date: {self.posted_date} < {self.transaction_date}"
            )

        # Category type validation
        if self.category.category_type != self.transaction_type:
            raise ValidationError(
                {
                    "category": _(
                        f"Category type ({self.category.category_type}) "
                        f"does not match transaction type ({self.transaction_type})."
                    )
                }
            )

        logger.debug(f"Transaction validation passed: {self.id}")

    def save(self, *args, **kwargs):
        """
        Override save to handle balance updates and transfer logic.
        """
        is_new = self.pk is None

        try:
            with transaction.atomic():
                # Save the transaction first
                super().save(*args, **kwargs)

                # Update account balance if transaction is completed
                if self.status == choices.TransactionStatus.COMPLETED:
                    success = self.account.update_balance(
                        self.amount, self.transaction_type
                    )

                    if not success:
                        logger.error(
                            f"Failed to update balance for transaction {self.id}"
                        )

                    # Handle transfer logic
                    if self.is_transfer and self.transfer_account:
                        self._create_transfer_pair()

                # Update category usage stats
                if is_new:
                    self.category.update_usage_stats()

                logger.info(f"Transaction saved: {self.id} - {self.name}")

        except Exception as e:
            logger.error(f"Error saving transaction {self.id}: {e}")
            raise

    def _create_transfer_pair(self) -> None:
        """Create paired transaction for transfers."""
        try:
            # Calculate amount in transfer account's currency
            transfer_amount = self._calculate_transfer_amount()

            if not transfer_amount:
                logger.error(
                    f"Cannot calculate transfer amount for transaction {self.id}"
                )
                return

            # Create the paired transaction
            paired_transaction = Transaction.objects.create(
                user=self.user,
                account=self.transfer_account,
                category=self.category,
                name=f"Transfer: {self.name}",
                transaction_type=(
                    choices.TransactionType.INCOME
                    if self.transaction_type == choices.TransactionType.EXPENSE
                    else choices.TransactionType.EXPENSE
                ),
                amount=transfer_amount,
                original_amount=self.original_amount,
                original_currency=self.original_currency,
                exchange_rate=self.exchange_rate,
                transaction_date=self.transaction_date,
                status=self.status,
                is_transfer=True,
                transfer_account=self.account,
                transfer_reference=self.id,
                description=f"Transfer from {self.account.name}",
            )

            # Update reference
            self.transfer_reference = paired_transaction.id
            self.save(update_fields=["transfer_reference", "updated_at"])

            logger.debug(
                f"Created transfer pair: {self.id} <-> {paired_transaction.id}"
            )

        except Exception as e:
            logger.error(f"Error creating transfer pair for transaction {self.id}: {e}")

    def _calculate_transfer_amount(self) -> Optional[Decimal]:
        """Calculate transfer amount in destination account's currency."""
        try:
            if self.account.currency.id == self.transfer_account.currency.id:
                # Same currency, no conversion needed
                return self.amount

            # Convert through base currency
            base_currency = Currency.get_base_currency()
            if not base_currency:
                logger.error("No base currency configured")
                return None

            # Convert source amount to base currency
            if self.account.currency.is_base_currency:
                amount_in_base = self.amount
            else:
                amount_in_base = self.amount / self.account.currency.exchange_rate

            # Convert from base to destination currency
            if self.transfer_account.currency.is_base_currency:
                return amount_in_base
            else:
                return amount_in_base * self.transfer_account.currency.exchange_rate

        except Exception as e:
            logger.error(f"Error calculating transfer amount: {e}")
            return None

    @property
    def converted_amount(self) -> Decimal:
        """Get converted amount in account currency."""
        if self.original_amount and self.exchange_rate != Decimal("1.0"):
            return self.original_amount * self.exchange_rate
        return self.amount

    @property
    def is_verified(self) -> bool:
        """Check if transaction is verified (completed or reconciled)."""
        return self.status in [
            choices.TransactionStatus.COMPLETED,
            choices.TransactionStatus.RECONCILED,
        ]

    def verify(self, user) -> bool:
        """
        Verify and complete a transaction.

        Args:
            user: User performing the verification

        Returns:
            True if verification successful
        """
        if self.status != choices.TransactionStatus.PENDING:
            logger.warning(f"Transaction {self.id} already in status {self.status}")
            return False

        try:
            self.status = choices.TransactionStatus.COMPLETED
            self.save(update_fields=["status", "updated_at"])

            # Update account balance
            self.account.update_balance(self.amount, self.transaction_type)

            logger.info(f"Transaction {self.id} verified by user {user.id}")
            return True

        except Exception as e:
            logger.error(f"Error verifying transaction {self.id}: {e}")
            return False

    def reconcile(self, reconciled_amount: Optional[Decimal] = None) -> bool:
        """
        Reconcile the transaction.

        Args:
            reconciled_amount: Reconciled amount (if different)

        Returns:
            True if reconciliation successful
        """
        try:
            self.status = choices.TransactionStatus.RECONCILED

            if reconciled_amount and reconciled_amount != self.amount:
                # Update with reconciled amount
                self.amount = reconciled_amount
                logger.info(
                    f"Transaction {self.id} reconciled with new amount: {reconciled_amount}"
                )

            self.save(update_fields=["status", "amount", "updated_at"])
            logger.info(f"Transaction {self.id} reconciled")
            return True

        except Exception as e:
            logger.error(f"Error reconciling transaction {self.id}: {e}")
            return False

    @classmethod
    def get_user_transactions_summary(
        cls,
        user_id: uuid.UUID,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> Dict[str, Any]:
        """
        Get transaction summary for a user.

        Args:
            user_id: User UUID
            start_date: Start date
            end_date: End date

        Returns:
            Transaction summary
        """
        try:
            query = cls.objects.filter(
                user_id=user_id, status=choices.TransactionStatus.COMPLETED
            )

            if start_date:
                query = query.filter(transaction_date__gte=start_date)
            if end_date:
                query = query.filter(transaction_date__lte=end_date)

            summary = query.aggregate(
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
                transaction_count=Count("id"),
            )

            net_flow = summary["total_income"] - summary["total_expense"]

            return {
                "total_income": summary["total_income"],
                "total_expense": summary["total_expense"],
                "net_flow": net_flow,
                "transaction_count": summary["transaction_count"],
                "period": {"start": start_date, "end": end_date},
            }

        except Exception as e:
            logger.error(f"Error getting transactions summary for user {user_id}: {e}")
            return {}


class Budget(utils_models.BaseModel):
    """
    Budget model for financial planning and tracking.

    Supports:
        - Category-specific budgets
        - Overall spending budgets
        - Time-based budgeting (monthly, quarterly, yearly)
        - Budget rollovers
        - Multi-currency support

    Key Metrics:
        - Total budgeted amount
        - Actual spending
        - Remaining balance
        - Utilization percentage
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="budgets",
        db_index=True,
        help_text=_("User who owns this budget"),
    )
    name = models.CharField(
        max_length=255, help_text=_('Name of the budget (e.g., "Monthly Groceries")')
    )
    budget_type = models.CharField(
        max_length=20,
        choices=choices.BudgetType.choices,
        default=choices.BudgetType.CATEGORY,
        db_index=True,
        help_text=_("Type of budget"),
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name="budgets",
        null=True,
        blank=True,
        help_text=_("Specific category for category budgets"),
    )

    # Budget amounts
    total_budget = models.DecimalField(
        max_digits=18, decimal_places=2, help_text=_("Total budgeted amount")
    )
    currency = models.ForeignKey(
        Currency,
        on_delete=models.PROTECT,
        related_name="budgets",
        help_text=_("Currency for this budget"),
    )

    # Calculated fields (updated via signals/methods)
    total_spent = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=_("Total amount spent in budget period"),
    )
    total_remaining = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=_("Remaining budget amount"),
    )

    # Period configuration
    period_type = models.CharField(
        max_length=20,
        choices=choices.PeriodType.choices,
        default=choices.PeriodType.MONTHLY,
        help_text=_("Budget period type"),
    )
    start_date = models.DateField(help_text=_("Start date of the budget period"))
    end_date = models.DateField(help_text=_("End date of the budget period"))

    # Settings
    rollover_unused = models.BooleanField(
        default=False, help_text=_("Roll over unused amount to next period")
    )
    rollover_amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=_("Amount rolled over from previous period"),
    )
    is_active = models.BooleanField(
        default=True, db_index=True, help_text=_("Whether this budget is active")
    )

    # Metadata
    description = models.TextField(
        blank=True, null=True, help_text=_("Detailed description of the budget")
    )
    notification_threshold = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("80.00"),
        help_text=_("Percentage threshold for notifications (e.g., 80 for 80%)"),
    )

    # Audit fields
    last_recalculated_at = models.DateTimeField(
        null=True, blank=True, help_text=_("When spending was last recalculated")
    )

    class Meta:
        verbose_name = _("Budget")
        verbose_name_plural = _("Budgets")
        ordering = ["-created_at"]
        db_table = "budgets"
        indexes = [
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["user", "budget_type"]),
            models.Index(fields=["start_date", "end_date"]),
            models.Index(fields=["category", "is_active"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(total_budget__gt=0),
                name=utils.get_index_name("budgets", ["total_budget"]),
            ),
            models.CheckConstraint(
                condition=models.Q(end_date__gt=models.F("start_date")),
                name=utils.get_index_name("budgets", ["end_date", "start_date"]),
            ),
            models.UniqueConstraint(
                fields=["user", "name"],
                condition=models.Q(is_active=True),
                name=utils.get_index_name("budgets", ["user", "name", "is_active"]),
            ),
        ]

    def __str__(self) -> str:
        """Human-readable string representation."""
        return f"{self.name} - {self.total_budget} {self.currency.code}"

    def clean(self) -> None:
        """Validate budget data."""
        logger.debug(f"Validating budget: {self.id}")

        # Validate dates
        if self.end_date <= self.start_date:
            logger.error(
                f"Budget end date must be after start date: {self.start_date} -> {self.end_date}"
            )
            raise ValidationError({"end_date": _("End date must be after start date.")})

        # Validate amount
        if self.total_budget <= 0:
            logger.error(f"Budget amount must be positive: {self.total_budget}")
            raise ValidationError(
                {"total_budget": _("Budget amount must be greater than zero.")}
            )

        # Validate category for category budgets
        if self.budget_type == choices.BudgetType.CATEGORY and not self.category:
            logger.error("Category budget must have a category")
            raise ValidationError(
                {"category": _("Category is required for category budgets.")}
            )

        # Validate no category for overall budgets
        if self.budget_type == choices.BudgetType.OVERALL and self.category:
            logger.warning("Overall budget should not have a specific category")
            self.category = None

        logger.debug(f"Budget validation passed: {self.id}")

    def save(self, *args, **kwargs) -> None:
        """Save with validation and automatic field calculation."""
        try:
            self.full_clean()

            # Calculate remaining before save
            self.total_remaining = (
                self.total_budget - self.total_spent + self.rollover_amount
            )

            with transaction.atomic():
                super().save(*args, **kwargs)
                logger.info(f"Budget saved: {self.id} - {self.name}")

        except Exception as e:
            logger.error(f"Error saving budget {self.id}: {e}")
            raise

    def calculate_spending(self) -> Decimal:
        """
        Calculate actual spending for this budget period.

        Returns:
            Total spending as Decimal
        """
        logger.debug(f"Calculating spending for budget: {self.id}")

        try:
            # Build query based on budget type
            query = Q(
                user=self.user,
                transaction_type=choices.TransactionType.EXPENSE,
                transaction_date__gte=self.start_date,
                transaction_date__lte=self.end_date,
                status=choices.TransactionStatus.COMPLETED,
            )

            if self.budget_type == choices.BudgetType.CATEGORY and self.category:
                query &= Q(category=self.category)

            # Calculate spending
            spending = self.user.transactions.filter(query).aggregate(
                total=Coalesce(Sum("amount"), Decimal("0.00"))
            )["total"]

            # Convert to budget currency if needed
            # Note: This is simplified - you might need actual currency conversion
            if spending != self.total_spent:
                logger.info(f"Spending recalculated for budget {self.id}: {spending}")
                self.total_spent = spending
                self.last_recalculated_at = timezone.now()
                self.save(
                    update_fields=["total_spent", "last_recalculated_at", "updated_at"]
                )

            return spending

        except Exception as e:
            logger.error(f"Error calculating spending for budget {self.id}: {e}")
            return Decimal("0.00")

    def get_utilization_percentage(self) -> float:
        """Calculate budget utilization percentage."""
        try:
            if self.total_budget + self.rollover_amount == 0:
                return 0.0

            total_available = self.total_budget + self.rollover_amount
            percentage = (self.total_spent / total_available) * 100
            return float(min(percentage, 100.0))

        except Exception as e:
            logger.error(f"Error calculating utilization for budget {self.id}: {e}")
            return 0.0

    def should_notify(self) -> bool:
        """Check if notification should be sent."""
        utilization = self.get_utilization_percentage()
        return utilization >= float(self.notification_threshold)

    def advance_period(self) -> bool:
        """
        Advance to next budget period with optional rollover.

        Returns:
            True if successful, False otherwise
        """
        logger.info(f"Advancing budget period: {self.id}")

        try:
            with transaction.atomic():
                # Calculate rollover if enabled
                new_rollover = Decimal("0.00")
                if self.rollover_unused:
                    new_rollover = max(self.total_remaining, Decimal("0.00"))

                # Calculate new period dates
                new_start = self.end_date + timedelta(days=1)

                if self.period_type == choices.PeriodType.MONTHLY:
                    new_end = new_start + timedelta(days=30)
                elif self.period_type == choices.PeriodType.QUARTERLY:
                    new_end = new_start + timedelta(days=90)
                elif self.period_type == choices.PeriodType.YEARLY:
                    new_end = new_start + timedelta(days=365)
                else:  # CUSTOM
                    period_days = (self.end_date - self.start_date).days
                    new_end = new_start + timedelta(days=period_days)

                # Update budget
                self.start_date = new_start
                self.end_date = new_end
                self.rollover_amount = new_rollover
                self.total_spent = Decimal("0.00")

                # Recalculate remaining
                self.total_remaining = self.total_budget + self.rollover_amount

                self.save()
                logger.info(f"Budget {self.id} advanced to new period")
                return True

        except Exception as e:
            logger.error(f"Error advancing budget period for {self.id}: {e}")
            return False

    @property
    def is_over_budget(self) -> bool:
        """Check if budget is exceeded."""
        return self.total_remaining < 0

    @property
    def days_remaining(self) -> int:
        """Calculate days remaining in budget period."""
        today = timezone.now().date()
        if today > self.end_date:
            return 0
        return (self.end_date - today).days


class BudgetCategory(models.Model):
    """
    Junction table for budgets with multiple categories.

    Allows for complex budgeting scenarios where a single budget
    covers multiple categories with individual allocations.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    budget = models.ForeignKey(
        Budget,
        on_delete=models.CASCADE,
        related_name="budget_categories",
        db_index=True,
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name="budget_categories",
        db_index=True,
    )

    # Allocation
    allocated_amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        help_text=_("Amount allocated to this category"),
    )

    # Calculated fields
    spent_amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=_("Amount spent in this category"),
    )
    remaining_amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=_("Remaining amount for this category"),
    )
    percentage_used = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=_("Percentage of allocation used (0-100)"),
    )

    class Meta:
        verbose_name = _("Budget Category")
        verbose_name_plural = _("Budget Categories")
        db_table = "budget_categories"
        unique_together = ["budget", "category"]
        indexes = [
            models.Index(fields=["budget", "category"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(allocated_amount__gt=0),
                name=utils.get_index_name("budget_categories", ["allocated_amount"]),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.category.name} - {self.allocated_amount} in {self.budget.name}"

    def clean(self) -> None:
        """Validate allocation."""
        if self.allocated_amount <= 0:
            raise ValidationError(
                {"allocated_amount": _("Allocated amount must be greater than zero.")}
            )

    def save(self, *args, **kwargs) -> None:
        """Save with automatic calculation."""
        self.remaining_amount = self.allocated_amount - self.spent_amount

        if self.allocated_amount > 0:
            self.percentage_used = (self.spent_amount / self.allocated_amount) * 100

        super().save(*args, **kwargs)

    def calculate_spending(self) -> Decimal:
        """Calculate spending for this category in budget period."""
        try:
            spending = self.budget.user.transactions.filter(
                category=self.category,
                transaction_type=choices.TransactionType.EXPENSE,
                transaction_date__gte=self.budget.start_date,
                transaction_date__lte=self.budget.end_date,
                status=choices.TransactionStatus.COMPLETED,
            ).aggregate(total=Coalesce(Sum("amount"), Decimal("0.00")))["total"]

            self.spent_amount = spending
            self.save(
                update_fields=[
                    "spent_amount",
                    "remaining_amount",
                    "percentage_used",
                    "updated_at",
                ]
            )

            return spending

        except Exception as e:
            logger.error(
                f"Error calculating spending for budget category {self.id}: {e}"
            )
            return Decimal("0.00")


class FinancialGoal(utils_models.BaseModel):
    """
    Model for tracking financial goals and savings targets.

    Examples:
        - Save for vacation
        - Emergency fund
        - Down payment for house
        - Retirement savings
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="financial_goals",
        db_index=True,
        help_text=_("User who owns this goal"),
    )
    name = models.CharField(max_length=255, help_text=_("Name of the financial goal"))
    description = models.TextField(
        blank=True, null=True, help_text=_("Detailed description of the goal")
    )
    goal_type = models.CharField(
        max_length=50,
        choices=choices.GoalType.choices,
        default=choices.GoalType.SAVINGS,
        db_index=True,
        help_text=_("Type of financial goal"),
    )

    # Amount tracking
    target_amount = models.DecimalField(
        max_digits=18, decimal_places=2, help_text=_("Target amount to achieve")
    )
    current_amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=_("Current amount saved/paid"),
    )
    currency = models.ForeignKey(
        Currency,
        on_delete=models.PROTECT,
        related_name="financial_goals",
        help_text=_("Currency for this goal"),
    )

    # Contribution planning
    monthly_contribution = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=_("Planned monthly contribution"),
    )

    # Timeline
    start_date = models.DateField(
        default=timezone.now, help_text=_("When the goal tracking started")
    )
    target_date = models.DateField(help_text=_("Target completion date"))
    achieved_date = models.DateField(
        null=True, blank=True, help_text=_("Date when goal was achieved")
    )

    # Priority and status
    priority = models.CharField(
        max_length=10,
        choices=choices.PriorityLevel.choices,
        default=choices.PriorityLevel.MEDIUM,
        db_index=True,
        help_text=_("Priority level of the goal"),
    )
    is_achieved = models.BooleanField(
        default=False, db_index=True, help_text=_("Whether the goal has been achieved")
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text=_("Whether this goal is actively being tracked"),
    )

    # Progress tracking
    progress_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=_("Percentage progress toward goal (0-100)"),
    )
    months_remaining = models.IntegerField(
        default=0, help_text=_("Estimated months remaining at current rate")
    )

    # Metadata
    linked_account = models.ForeignKey(
        Account,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="financial_goals",
        help_text=_("Account linked to this goal (optional)"),
    )

    class Meta:
        verbose_name = _("Financial Goal")
        verbose_name_plural = _("Financial Goals")
        ordering = ["priority", "-target_date"]
        db_table = "financial_goals"
        indexes = [
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["user", "goal_type"]),
            models.Index(fields=["target_date"]),
            models.Index(fields=["priority", "target_date"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(target_amount__gt=0),
                name=utils.get_index_name("financial_goals", ["target_amount"]),
            ),
            models.CheckConstraint(
                condition=models.Q(current_amount__gte=0),
                name=utils.get_index_name("financial_goals", ["current_amount"]),
            ),
            models.CheckConstraint(
                condition=models.Q(target_date__gt=models.F("start_date")),
                name=utils.get_index_name(
                    "financial_goals", ["target_date", "start_date"]
                ),
            ),
        ]

    def __str__(self) -> str:
        status = "✓" if self.is_achieved else "→"
        return f"{status} {self.name} - {self.current_amount}/{self.target_amount} {self.currency.code}"

    def clean(self) -> None:
        """Validate goal data."""
        logger.debug(f"Validating financial goal: {self.id}")

        # Validate amounts
        if self.target_amount <= 0:
            logger.error(f"Goal target amount must be positive: {self.target_amount}")
            raise ValidationError(
                {"target_amount": _("Target amount must be greater than zero.")}
            )

        if self.current_amount < 0:
            logger.error(f"Current amount cannot be negative: {self.current_amount}")
            raise ValidationError(
                {"current_amount": _("Current amount cannot be negative.")}
            )

        if self.current_amount > self.target_amount:
            logger.warning("Current amount exceeds target amount")
            self.current_amount = self.target_amount

        # Validate dates
        if self.target_date <= self.start_date:
            logger.error("Target date must be after start date")
            raise ValidationError(
                {"target_date": _("Target date must be after start date.")}
            )

        # If achieved, set achieved date
        if self.is_achieved and not self.achieved_date:
            self.achieved_date = timezone.now().date()

        logger.debug(f"Financial goal validation passed: {self.id}")

    def save(self, *args, **kwargs) -> None:
        """Save with automatic progress calculation."""
        try:
            self.full_clean()

            # Calculate progress
            if self.target_amount > 0:
                self.progress_percentage = (
                    self.current_amount / self.target_amount
                ) * 100

            # Check if goal is achieved
            if not self.is_achieved and self.current_amount >= self.target_amount:
                self.is_achieved = True
                self.achieved_date = self.achieved_date or timezone.now().date()
                logger.info(f"Goal achieved: {self.id} - {self.name}")

            # Calculate months remaining
            self._calculate_months_remaining()

            with transaction.atomic():
                super().save(*args, **kwargs)
                logger.debug(f"Financial goal saved: {self.id}")

        except Exception as e:
            logger.error(f"Error saving financial goal {self.id}: {e}")
            raise

    def _calculate_months_remaining(self) -> None:
        """Calculate estimated months remaining at current contribution rate."""
        try:
            if self.is_achieved:
                self.months_remaining = 0
                return

            amount_needed = self.target_amount - self.current_amount

            if self.monthly_contribution > 0:
                months = amount_needed / self.monthly_contribution
                self.months_remaining = max(
                    1, int(months.quantize(Decimal("1"), rounding="ROUND_UP"))
                )
            else:
                # No monthly contribution set
                today = timezone.now().date()
                if today < self.target_date:
                    # Estimate based on time
                    delta = self.target_date - today
                    self.months_remaining = max(1, delta.days // 30)
                else:
                    self.months_remaining = 0

        except Exception as e:
            logger.error(f"Error calculating months remaining for goal {self.id}: {e}")
            self.months_remaining = 0

    def add_contribution(self, amount: Decimal, date: Optional[date] = None) -> bool:
        """
        Add a contribution to the goal.

        Args:
            amount: Contribution amount (positive)
            date: Date of contribution (defaults to today)

        Returns:
            True if contribution added successfully
        """
        logger.info(f"Adding contribution of {amount} to goal {self.id}")

        if amount <= 0:
            logger.error(f"Contribution amount must be positive: {amount}")
            return False

        try:
            with transaction.atomic():
                self.current_amount += amount
                self.save()

                # Create a transaction record if linked to account
                if self.linked_account:
                    from .models import Transaction

                    Transaction.objects.create(
                        user=self.user,
                        account=self.linked_account,
                        category=Category.objects.get_or_create(
                            user=self.user, name="Savings", category_type="savings"
                        )[0],
                        name=f"Goal Contribution: {self.name}",
                        transaction_type=choices.TransactionType.EXPENSE,
                        amount=amount,
                        currency=self.currency.code,
                        description=f"Contribution to {self.name} goal",
                        transaction_date=date or timezone.now().date(),
                        status=choices.TransactionStatus.COMPLETED,
                        tags=["goal-contribution", self.goal_type],
                    )

                logger.info(f"Contribution of {amount} added to goal {self.id}")
                return True

        except Exception as e:
            logger.error(f"Error adding contribution to goal {self.id}: {e}")
            return False

    def get_progress_summary(self) -> Dict[str, Any]:
        """Get detailed progress summary."""
        try:
            amount_needed = self.target_amount - self.current_amount

            # Calculate average monthly needed
            today = timezone.now().date()
            if today < self.target_date:
                months_left = max(1, (self.target_date - today).days // 30)
                monthly_needed = amount_needed / Decimal(months_left)
            else:
                months_left = 0
                monthly_needed = amount_needed

            return {
                "goal_id": str(self.id),
                "name": self.name,
                "current_amount": self.current_amount,
                "target_amount": self.target_amount,
                "amount_needed": amount_needed,
                "progress_percentage": float(self.progress_percentage),
                "is_achieved": self.is_achieved,
                "months_remaining": self.months_remaining,
                "months_left": months_left,
                "monthly_needed": float(monthly_needed),
                "monthly_contribution": float(self.monthly_contribution),
                "on_track": (
                    self.monthly_contribution >= monthly_needed
                    if months_left > 0
                    else True
                ),
            }

        except Exception as e:
            logger.error(f"Error getting progress summary for goal {self.id}: {e}")
            return {"error": str(e)}


class Report(utils_models.BaseModel):
    """
    Model for storing generated financial reports.

    Supports various report types with configurable parameters
    and automatic cleanup of old reports.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reports",
        db_index=True,
        help_text=_("User who requested this report"),
    )

    # Report identification
    report_name = models.CharField(max_length=255, help_text=_("Name of the report"))
    report_type = models.CharField(
        max_length=50,
        choices=choices.ReportType.choices,
        db_index=True,
        help_text=_("Type of report"),
    )

    # Period covered
    period_start = models.DateField(help_text=_("Start date of report period"))
    period_end = models.DateField(help_text=_("End date of report period"))

    # Report configuration
    parameters = models.JSONField(
        default=dict, blank=True, help_text=_("Configuration parameters for the report")
    )
    format = models.CharField(
        max_length=10,
        choices=choices.ReportFormat.choices,
        default=choices.ReportFormat.JSON,
        help_text=_("Output format of the report"),
    )

    # Generated data
    data = models.JSONField(
        null=True, blank=True, help_text=_("Generated report data (for JSON format)")
    )
    file = models.FileField(
        upload_to="reports/%Y/%m/%d/",
        null=True,
        blank=True,
        max_length=500,
        help_text=_("Generated report file"),
    )
    file_size = models.BigIntegerField(
        null=True, blank=True, help_text=_("Size of the generated file in bytes")
    )

    # Generation metadata
    status = models.CharField(
        max_length=20,
        choices=choices.ReportStatus.choices,
        default=choices.ReportStatus.PENDING,
        db_index=True,
        help_text=_("Current status of report generation"),
    )
    error_message = models.TextField(
        blank=True, null=True, help_text=_("Error message if generation failed")
    )
    generation_duration = models.FloatField(
        null=True, blank=True, help_text=_("Time taken to generate report (seconds)")
    )

    # Checksum for data integrity
    checksum = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        help_text=_("SHA-256 checksum of report data/file"),
    )

    # Expiry for automatic cleanup
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text=_("When this report should be automatically deleted"),
    )

    generated_at = models.DateTimeField(
        null=True, blank=True, help_text=_("When the report was generated")
    )

    class Meta:
        verbose_name = _("Report")
        verbose_name_plural = _("Reports")
        ordering = ["-created_at"]
        db_table = "reports"
        indexes = [
            models.Index(fields=["user", "report_type"]),
            models.Index(fields=["user", "status"]),
            models.Index(fields=["expires_at"]),
            models.Index(fields=["created_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(period_end__gt=models.F("period_start")),
                name=utils.get_index_name("reports", ["period_end", "period_start"]),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.report_name} ({self.get_report_type_display()}) - {self.get_status_display()}"

    def clean(self) -> None:
        """Validate report configuration."""
        logger.debug(f"Validating report: {self.id}")

        # Validate period
        if self.period_end <= self.period_start:
            logger.error(
                f"Report period end must be after start: {self.period_start} -> {self.period_end}"
            )
            raise ValidationError(
                {"period_end": _("Period end must be after period start.")}
            )

        # Set default expiry (30 days from creation)
        if not self.expires_at and self.status == choices.ReportStatus.COMPLETED:
            self.expires_at = timezone.now() + timedelta(days=30)

        logger.debug(f"Report validation passed: {self.id}")

    def save(self, *args, **kwargs) -> None:
        """Save with validation and checksum calculation."""
        try:
            self.full_clean()

            # Calculate checksum if we have data or file
            if self.data or self.file:
                self.checksum = self._calculate_checksum()

            # Update file size if file exists
            if self.file and self.file.size:
                self.file_size = self.file.size

            with transaction.atomic():
                super().save(*args, **kwargs)
                logger.debug(f"Report saved: {self.id}")

        except Exception as e:
            logger.error(f"Error saving report {self.id}: {e}")
            raise

    def _calculate_checksum(self) -> str:
        """Calculate SHA-256 checksum of report data or file."""
        try:
            content = None

            if self.data:
                # Use JSON data
                content = json.dumps(self.data, sort_keys=True).encode("utf-8")
            elif self.file and self.file.file:
                # Use file content
                self.file.file.seek(0)
                content = self.file.file.read()
                self.file.file.seek(0)

            if content:
                return hashlib.sha256(content).hexdigest()

        except Exception as e:
            logger.error(f"Error calculating checksum for report {self.id}: {e}")

        return ""

    def mark_as_processing(self) -> bool:
        """Mark report as processing."""
        try:
            self.status = self.ReportStatus.PROCESSING
            self.save(update_fields=["status", "updated_at"])
            logger.info(f"Report {self.id} marked as processing")
            return True
        except Exception as e:
            logger.error(f"Error marking report {self.id} as processing: {e}")
            return False

    def mark_as_completed(
        self,
        data: Optional[Dict] = None,
        file_path: Optional[str] = None,
        duration: Optional[float] = None,
    ) -> bool:
        """
        Mark report as completed with generated data.

        Args:
            data: Generated report data (for JSON format)
            file_path: Path to generated file
            duration: Generation duration in seconds

        Returns:
            True if successfully marked as completed
        """
        logger.info(f"Marking report {self.id} as completed")

        try:
            self.status = self.ReportStatus.COMPLETED
            self.generated_at = timezone.now()

            if data is not None:
                self.data = data

            if duration is not None:
                self.generation_duration = duration

            if file_path:
                # In a real implementation, you'd handle file storage here
                pass

            # Set expiry if not set
            if not self.expires_at:
                self.expires_at = timezone.now() + timedelta(days=30)

            self.save()
            logger.info(f"Report {self.id} completed successfully")
            return True

        except Exception as e:
            logger.error(f"Error marking report {self.id} as completed: {e}")
            self.status = self.ReportStatus.FAILED
            self.error_message = str(e)
            self.save(update_fields=["status", "error_message", "updated_at"])
            return False

    def mark_as_failed(self, error: str) -> None:
        """Mark report as failed with error message."""
        self.status = self.ReportStatus.FAILED
        self.error_message = error[:1000]  # Limit error message length
        self.save(update_fields=["status", "error_message", "updated_at"])
        logger.error(f"Report {self.id} marked as failed: {error}")

    def is_expired(self) -> bool:
        """Check if report has expired."""
        if not self.expires_at:
            return False
        return timezone.now() > self.expires_at

    def get_download_url(self) -> Optional[str]:
        """Get download URL for the report."""
        if self.file and hasattr(self.file, "url"):
            return self.file.url
        return None

    @classmethod
    def cleanup_expired_reports(cls) -> Dict[str, Any]:
        """
        Delete expired reports.

        Returns:
            Dictionary with cleanup statistics
        """
        logger.info("Cleaning up expired reports")

        stats = {"total_expired": 0, "deleted": 0, "errors": []}

        try:
            expired_reports = cls.objects.filter(expires_at__lt=timezone.now())

            stats["total_expired"] = expired_reports.count()

            for report in expired_reports:
                try:
                    # Delete associated file if exists
                    if report.file:
                        report.file.delete(save=False)

                    report.delete()
                    stats["deleted"] += 1
                    logger.debug(f"Deleted expired report: {report.id}")

                except Exception as e:
                    stats["errors"].append(
                        {"report_id": str(report.id), "error": str(e)}
                    )
                    logger.error(f"Error deleting report {report.id}: {e}")

            logger.info(
                f"Cleanup completed: {stats['deleted']}/{stats['total_expired']} reports deleted"
            )
            return stats

        except Exception as e:
            logger.error(f"Error in report cleanup: {e}")
            stats["errors"].append({"cleanup_error": str(e)})
            return stats
