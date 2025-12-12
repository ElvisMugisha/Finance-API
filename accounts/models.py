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


class RecurringTransaction(models.Model):
    """
    Model for managing repeating income or expenses.

    Examples:
        - Monthly salary
        - Rent payments
        - Subscription services
        - Loan repayments

    Key Features:
        - Flexible scheduling (daily, weekly, monthly, etc.)
        - Support for end dates or max occurrences
        - Automatic next occurrence calculation
        - Graceful error handling for generation failures
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="recurring_transactions",
        db_index=True,
        help_text=_("User who owns this recurring transaction"),
    )
    account = models.ForeignKey(
        Account,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recurring_transactions",
        help_text=_("Account to use for generated transactions (optional)"),
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="recurring_transactions",
        help_text=_("Category for generated transactions"),
    )

    # Transaction details
    name = models.CharField(
        max_length=255, help_text=_("Name of the recurring transaction")
    )
    transaction_type = models.CharField(
        max_length=10,
        choices=choices.TransactionType.choices,
        db_index=True,
        help_text=_("Type of transaction (income or expense)"),
    )
    amount = models.DecimalField(
        max_digits=18, decimal_places=2, help_text=_("Amount for each occurrence")
    )
    currency = models.CharField(
        max_length=3,
        default="USD",
        db_index=True,
        help_text=_("Currency code (ISO 4217)"),
    )
    description = models.CharField(
        max_length=255, blank=True, null=True, help_text=_("Short description")
    )
    notes = models.TextField(
        blank=True, null=True, help_text=_("Additional notes or details")
    )

    # Scheduling configuration
    frequency = models.CharField(
        max_length=20,
        choices=choices.FrequencyType.choices,
        default=choices.FrequencyType.MONTHLY,
        help_text=_("How often the transaction repeats"),
    )
    interval = models.PositiveIntegerField(
        default=1, help_text=_("Every N periods (e.g., 2 for every 2 weeks)")
    )
    day_of_month = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(31)],
        help_text=_("Day of month for monthly transactions (1-31)"),
    )
    day_of_week = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(7)],
        choices=[
            (i, day)
            for i, day in enumerate(
                [
                    "Monday",
                    "Tuesday",
                    "Wednesday",
                    "Thursday",
                    "Friday",
                    "Saturday",
                    "Sunday",
                ],
                1,
            )
        ],
        help_text=_("Day of week for weekly transactions"),
    )

    # Date management
    start_date = models.DateField(
        default=timezone.now,
        help_text=_("Date when recurring transactions should start"),
    )
    end_date = models.DateField(
        null=True,
        blank=True,
        help_text=_("Optional end date for the recurring transaction"),
    )
    next_occurrence = models.DateField(
        db_index=True, help_text=_("Next date when transaction should be generated")
    )

    # Occurrence tracking
    occurrences_created = models.PositiveIntegerField(
        default=0, help_text=_("Number of transactions already generated")
    )
    max_occurrences = models.PositiveIntegerField(
        null=True, blank=True, help_text=_("Maximum number of occurrences (optional)")
    )

    # Status and metadata
    is_transfer = models.BooleanField(
        default=False, help_text=_("Whether this is a transfer between accounts")
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text=_("Whether this recurring transaction is active"),
    )
    tags = models.JSONField(
        default=list, blank=True, help_text=_("Tags for categorization")
    )
    attachments = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Attachment metadata (paths, descriptions)"),
    )

    # Audit fields
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_generated_at = models.DateTimeField(
        null=True, blank=True, help_text=_("When the last transaction was generated")
    )

    class Meta:
        verbose_name = _("Recurring Transaction")
        verbose_name_plural = _("Recurring Transactions")
        ordering = ["next_occurrence", "-created_at"]
        db_table = "recurring_transactions"
        indexes = [
            models.Index(fields=["user", "is_active", "next_occurrence"]),
            models.Index(fields=["user", "frequency"]),
            models.Index(fields=["next_occurrence", "is_active"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="recurring_transaction_amount_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(interval__gte=1),
                name="recurring_transaction_interval_minimum",
            ),
            models.CheckConstraint(
                condition=models.Q(end_date__isnull=True)
                | models.Q(end_date__gt=models.F("start_date")),
                name="recurring_transaction_valid_end_date",
            ),
        ]

    def __str__(self) -> str:
        """Human-readable string representation."""
        return f"{self.name} - {self.amount} {self.currency} ({self.get_frequency_display()})"

    def clean(self) -> None:
        """
        Validate the recurring transaction configuration.

        Raises:
            ValidationError: If configuration is invalid

        Logs:
            Warnings for questionable configurations
        """
        logger.debug(f"Validating recurring transaction: {self.id}")

        # Validate amount
        if self.amount <= 0:
            logger.error(
                f"Recurring transaction amount must be positive: {self.amount}"
            )
            raise ValidationError({"amount": _("Amount must be greater than zero.")})

        # Validate interval
        if self.interval < 1:
            logger.error(f"Invalid interval: {self.interval}")
            raise ValidationError({"interval": _("Interval must be at least 1.")})

        # Validate date logic
        if self.end_date and self.end_date <= self.start_date:
            logger.error(
                f"End date must be after start date: {self.start_date} -> {self.end_date}"
            )
            raise ValidationError({"end_date": _("End date must be after start date.")})

        # Frequency-specific validations
        if self.frequency == choices.FrequencyType.MONTHLY and not self.day_of_month:
            logger.warning("Monthly recurring transaction without day_of_month set")
            # Default to start date's day if not set
            if not self.day_of_month:
                self.day_of_month = self.start_date.day

        if self.frequency == choices.FrequencyType.WEEKLY and not self.day_of_week:
            logger.warning("Weekly recurring transaction without day_of_week set")
            # Default to start date's weekday if not set
            if not self.day_of_week:
                # Monday=1, Sunday=7
                self.day_of_week = self.start_date.isoweekday()

        # Validate day_of_month for monthly
        if self.day_of_month and self.day_of_month > 31:
            logger.error(f"Invalid day_of_month: {self.day_of_month}")
            raise ValidationError(
                {"day_of_month": _("Day of month must be between 1 and 31.")}
            )

        # Set next occurrence if not set
        if not self.next_occurrence:
            self.next_occurrence = self.start_date

        logger.debug(f"Recurring transaction validation passed: {self.id}")

    def save(self, *args, **kwargs) -> None:
        """
        Save with validation and automatic next occurrence calculation.

        Raises:
            ValidationError: If validation fails
            DatabaseError: If database constraints are violated
        """
        try:
            self.full_clean()

            with transaction.atomic():
                # Ensure next_occurrence is calculated
                if not self.next_occurrence:
                    self.next_occurrence = self.calculate_next_occurrence()

                super().save(*args, **kwargs)
                logger.info(f"Recurring transaction saved: {self.id} - {self.name}")

        except ValidationError as ve:
            logger.error(f"Validation failed for recurring transaction {self.id}: {ve}")
            raise
        except DatabaseError as de:
            logger.error(f"Database error saving recurring transaction {self.id}: {de}")
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error saving recurring transaction {self.id}: {e}"
            )
            raise

    def calculate_next_occurrence(self, from_date: Optional[date] = None) -> date:
        """
        Calculate the next occurrence date based on frequency and interval.

        Args:
            from_date: Date to calculate from (defaults to next_occurrence or today)

        Returns:
            Calculated next occurrence date

        Raises:
            ValueError: If frequency type is invalid
        """
        if from_date is None:
            from_date = self.next_occurrence or timezone.now().date()

        logger.debug(
            f"Calculating next occurrence from {from_date} with frequency {self.frequency}"
        )

        try:
            if self.frequency == choices.FrequencyType.DAILY:
                return from_date + timedelta(days=self.interval)

            elif self.frequency == choices.FrequencyType.WEEKLY:
                days_to_add = self.interval * 7
                return from_date + timedelta(days=days_to_add)

            elif self.frequency == choices.FrequencyType.BI_WEEKLY:
                return from_date + timedelta(weeks=2 * self.interval)

            elif self.frequency == choices.FrequencyType.MONTHLY:
                # Handle month arithmetic
                year = from_date.year
                month = from_date.month + self.interval

                # Adjust year if month exceeds 12
                while month > 12:
                    month -= 12
                    year += 1

                # Handle day_of_month (e.g., 31st in February)
                max_day = self._get_days_in_month(year, month)
                day = min(self.day_of_month or from_date.day, max_day)

                return date(year, month, day)

            elif self.frequency == choices.FrequencyType.QUARTERLY:
                months_to_add = self.interval * 3
                year = from_date.year
                month = from_date.month + months_to_add

                while month > 12:
                    month -= 12
                    year += 1

                max_day = self._get_days_in_month(year, month)
                day = min(self.day_of_month or from_date.day, max_day)

                return date(year, month, day)

            elif self.frequency == choices.FrequencyType.YEARLY:
                year = from_date.year + self.interval
                month = from_date.month
                day = from_date.day

                # Handle leap year for Feb 29
                if month == 2 and day == 29:
                    # Check if target year is leap year
                    if not self._is_leap_year(year):
                        day = 28

                return date(year, month, day)

            else:
                logger.error(f"Invalid frequency type: {self.frequency}")
                raise ValueError(f"Invalid frequency type: {self.frequency}")

        except Exception as e:
            logger.error(f"Error calculating next occurrence for {self.id}: {e}")
            # Fallback to adding 30 days
            return from_date + timedelta(days=30)

    def _get_days_in_month(self, year: int, month: int) -> int:
        """Helper to get number of days in a month."""
        if month == 2:
            return 29 if self._is_leap_year(year) else 28
        elif month in [4, 6, 9, 11]:
            return 30
        else:
            return 31

    def _is_leap_year(self, year: int) -> bool:
        """Helper to check if a year is a leap year."""
        return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)

    def should_generate_today(self) -> bool:
        """Check if a transaction should be generated today."""
        today = timezone.now().date()

        # Check if we're past the end date
        if self.end_date and today > self.end_date:
            return False

        # Check max occurrences
        if self.max_occurrences and self.occurrences_created >= self.max_occurrences:
            return False

        # Check if today is the next occurrence
        return today >= self.next_occurrence

    def generate_transaction(self) -> Optional["Transaction"]:
        """
        Generate a transaction instance from this recurring template.

        Returns:
            Transaction instance (unsaved) or None if generation fails

        Logs:
            Success/failure of transaction generation
        """
        logger.info(f"Generating transaction from recurring template: {self.id}")

        try:
            if not self.should_generate_today():
                logger.debug(f"Not generating transaction for {self.id} - not due yet")
                return None

            # Create transaction instance
            transaction_data = {
                "user": self.user,
                "account": self.account,
                "category": self.category,
                "name": self.name,
                "transaction_type": self.transaction_type,
                "amount": self.amount,
                "currency": self.currency,
                "description": self.description
                or f"Auto-generated from recurring: {self.name}",
                "notes": self.notes,
                "is_transfer": self.is_transfer,
                "tags": self.tags.copy(),
                "attachments": self.attachments.copy(),
                "transaction_date": self.next_occurrence,
                "status": choices.TransactionStatus.COMPLETED,
            }

            # Create transaction (don't save yet - let caller decide)
            from .models import Transaction

            generated_transaction = Transaction(**transaction_data)

            # Update recurring transaction
            self.occurrences_created += 1
            self.last_generated_at = timezone.now()
            self.next_occurrence = self.calculate_next_occurrence(self.next_occurrence)

            # Check if we should deactivate
            if (self.end_date and self.next_occurrence > self.end_date) or (
                self.max_occurrences
                and self.occurrences_created >= self.max_occurrences
            ):
                self.is_active = False
                logger.info(
                    f"Deactivating recurring transaction {self.id} - limit reached"
                )

            # Save recurring transaction updates
            self.save(
                update_fields=[
                    "occurrences_created",
                    "last_generated_at",
                    "next_occurrence",
                    "is_active",
                    "updated_at",
                ]
            )

            logger.info(f"Successfully generated transaction from {self.id}")
            return generated_transaction

        except Exception as e:
            logger.error(f"Failed to generate transaction from {self.id}: {e}")
            return None

    @classmethod
    def generate_due_transactions(
        cls, user_id: Optional[uuid.UUID] = None
    ) -> Dict[str, Any]:
        """
        Generate all due transactions for a user or all users.

        Args:
            user_id: Optional user ID to limit generation

        Returns:
            Dictionary with generation statistics
        """
        logger.info(f"Generating due transactions for user: {user_id or 'all users'}")

        query = Q(is_active=True, next_occurrence__lte=timezone.now().date())
        if user_id:
            query &= Q(user_id=user_id)

        stats = {
            "total_processed": 0,
            "successful": 0,
            "failed": 0,
            "generated_transactions": [],
            "errors": [],
        }

        try:
            recurring_transactions = cls.objects.filter(query).select_related(
                "user", "account", "category"
            )

            for recurring in recurring_transactions:
                stats["total_processed"] += 1

                try:
                    with transaction.atomic():
                        generated = recurring.generate_transaction()
                        if generated:
                            # Save the generated transaction
                            generated.save()
                            stats["successful"] += 1
                            stats["generated_transactions"].append(str(generated.id))
                            logger.debug(
                                f"Generated transaction {generated.id} from {recurring.id}"
                            )
                        else:
                            stats["failed"] += 1
                            logger.warning(f"Failed to generate from {recurring.id}")

                except Exception as e:
                    stats["failed"] += 1
                    stats["errors"].append(
                        {"recurring_id": str(recurring.id), "error": str(e)}
                    )
                    logger.error(f"Error generating from {recurring.id}: {e}")

            logger.info(
                f"Generation complete: {stats['successful']} successful, {stats['failed']} failed"
            )
            return stats

        except Exception as e:
            logger.error(f"Failed to generate due transactions: {e}")
            stats["errors"].append({"batch_error": str(e)})
            return stats


class Budget(models.Model):
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
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
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
                name="budget_total_budget_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(end_date__gt=models.F("start_date")),
                name="budget_end_date_after_start_date",
            ),
            models.UniqueConstraint(
                fields=["user", "name"],
                condition=models.Q(is_active=True),
                name="unique_active_budget_name_per_user",
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
                name="budget_category_allocated_amount_positive",
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


class FinancialGoal(models.Model):
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

    # Audit fields
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

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
                name="financial_goal_target_amount_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(current_amount__gte=0),
                name="financial_goal_current_amount_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(target_date__gt=models.F("start_date")),
                name="financial_goal_target_date_after_start",
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


class Report(models.Model):
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

    # Audit fields
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
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
                name="report_period_end_after_start",
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
