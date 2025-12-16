import uuid
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from utils.models import BaseModel
from utils import choices, loggings, utils

# Initialize logger
logger = loggings.setup_logging()


class Currency(BaseModel):
    """
    ISO 4217 currency catalog with full audit trail and exchange rate management.

    Key Features:
    - Strict ISO 4217 code validation
    - Exchange rate tracking with source attribution
    - Historical rate support (via JSON field)
    - Active/inactive status for soft deletes
    """

    id = models.BigAutoField(primary_key=True, editable=False)

    code = models.CharField(
        max_length=3,
        unique=True,
        db_index=True,
        help_text=_("ISO 4217 currency code (e.g., USD, EUR)"),
    )
    name = models.CharField(
        max_length=100,
        db_index=True,
        help_text=_("Full currency name (e.g., US Dollar)"),
    )
    symbol = models.CharField(
        max_length=5, null=True, blank=True, help_text=_("Currency symbol (e.g., $, €)")
    )

    decimal_places = models.PositiveSmallIntegerField(
        default=2,
        validators=[MinValueValidator(0), MaxValueValidator(10)],
        help_text=_("Number of decimal places for this currency"),
    )
    exchange_rate = models.DecimalField(
        max_digits=20,
        decimal_places=10,  # Increased for crypto currencies
        default=Decimal("1.0"),
        help_text=_("Exchange rate to base currency"),
    )
    exchange_source = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        help_text=_("Source of exchange rate (e.g., 'ECB', 'OpenExchangeRates')"),
    )
    exchange_updated_at = models.DateTimeField(
        null=True, blank=True, help_text=_("When exchange rate was last updated")
    )

    # Historical rates (stored as JSON for easy querying)
    historical_rates = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Historical exchange rates with timestamps"),
    )

    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text=_("Whether this currency is active for use"),
    )
    is_base_currency = models.BooleanField(
        default=False, help_text=_("Whether this is the system's base currency")
    )

    def __str__(self) -> str:
        """Human-readable representation."""
        if self.symbol:
            return f"{self.code} ({self.symbol}) - {self.name}"
        return f"{self.code} - {self.name}"

    def clean(self) -> None:
        """
        Validate currency fields.

        Raises:
            ValidationError: If validation fails
        """
        logger.debug(f"Validating currency: {self.code}")

        # ISO 4217 code validation
        if not self.code or len(self.code) != 3:
            raise ValidationError(
                {"code": _("Currency code must be exactly 3 characters.")}
            )

        if not self.code.isalpha():
            raise ValidationError(
                {"code": _("Currency code must contain only letters.")}
            )

        self.code = self.code.upper()

        # Exchange rate validation
        if self.exchange_rate <= 0:
            raise ValidationError(
                {"exchange_rate": _("Exchange rate must be positive.")}
            )

        # Ensure only one base currency
        if self.is_base_currency:
            existing_base = (
                Currency.objects.filter(is_base_currency=True)
                .exclude(pk=self.pk)
                .first()
            )

            if existing_base:
                logger.warning(
                    f"Multiple base currencies detected. "
                    f"Existing: {existing_base.code}, New: {self.code}"
                )

        logger.debug(f"Currency validation passed: {self.code}")

    def convert_amount(
        self, amount: Decimal, target_currency: "Currency", date: Optional[date] = None
    ) -> Optional[Decimal]:
        """
        Convert amount from this currency to target currency.

        Args:
            amount: Amount to convert
            target_currency: Target currency instance
            date: Date for historical rate (optional)

        Returns:
            Converted amount or None if conversion fails
        """
        if not isinstance(amount, Decimal):
            try:
                amount = Decimal(str(amount))
            except (InvalidOperation, TypeError):
                logger.error(f"Invalid amount for conversion: {amount}")
                return None

        # Same currency, no conversion needed
        if self.id == target_currency.id:
            return amount

        try:
            # Get exchange rate (historical or current)
            rate = self._get_exchange_rate(target_currency, date)

            if not rate:
                logger.error(
                    f"No exchange rate found for {self.code} -> {target_currency.code}"
                )
                return None

            # Perform conversion with proper rounding
            converted = amount * rate
            return converted.quantize(
                Decimal(f"0.{'0' * target_currency.decimal_places}"),
                rounding=ROUND_HALF_UP,
            )

        except Exception as e:
            logger.error(f"Currency conversion error: {e}")
            return None

    def _get_exchange_rate(
        self, target_currency: "Currency", date: Optional[date] = None
    ) -> Optional[Decimal]:
        """Get exchange rate between two currencies."""
        # TODO: Implement historical rate lookup
        # For now, use current rates
        if self.is_base_currency:
            # Base (1.0) -> Target (Rate).
            # If Target Rate is "Value in Base", then 1 Base = 1/Rate Target?
            # Standard: Rate is "How many units of this currency match 1 Base"
            # OR Rate is "How much is 1 unit of this currency in Base".
            #
            # Based on Test Expectation:
            # 100 EUR (0.85) -> 85 USD (1.0). (0.85 USD per EUR).
            # So Rate = Value in Base.
            #
            # If Self is Base (1.0):
            # 100 USD -> EUR?
            # 1 EUR = 0.85 USD.
            # 1 USD = 1/0.85 EUR.
            # So Rate = 1 / TargetRate.
            if target_currency.exchange_rate == 0:
                return None
            return Decimal("1.0") / target_currency.exchange_rate

        elif target_currency.is_base_currency:
            # Self (Rate) -> Base (1.0).
            # 100 EUR -> USD.
            # Rate is 0.85 (USD per EUR).
            # So Rate = Self.Rate.
            return self.exchange_rate

        else:
            # EUR (0.85) -> GBP (0.75).
            # EUR -> Base -> GBP.
            # Rate = Self.Rate (to Base) * (1/Target.Rate) (Base to Target).
            # Rate = Self.Rate / Target.Rate.
            if target_currency.exchange_rate == 0:
                return None
            return self.exchange_rate / target_currency.exchange_rate

    @classmethod
    def get_base_currency(cls) -> Optional["Currency"]:
        """Get the system's base currency."""
        return cls.objects.filter(is_base_currency=True).first()

    @classmethod
    def update_exchange_rates(
        cls, rates: Dict[str, Decimal], source: str
    ) -> Dict[str, Any]:
        """
        Batch update exchange rates.

        Args:
            rates: Dictionary of currency_code -> exchange_rate
            source: Source of the rates

        Returns:
            Update statistics
        """
        logger.info(f"Updating exchange rates from {source}: {len(rates)} currencies")

        stats = {"updated": 0, "failed": 0, "errors": []}

        try:
            with transaction.atomic():
                for code, rate in rates.items():
                    try:
                        currency = cls.objects.get(code=code.upper())

                        # Store historical rate
                        historical_entry = {
                            "rate": str(currency.exchange_rate),
                            "date": timezone.now().isoformat(),
                            "source": currency.exchange_source or "unknown",
                        }

                        currency.historical_rates.append(historical_entry)

                        # Limit historical rates to last 30 entries
                        if len(currency.historical_rates) > 30:
                            currency.historical_rates = currency.historical_rates[-30:]

                        # Update current rate
                        currency.exchange_rate = rate
                        currency.exchange_source = source
                        currency.exchange_updated_at = timezone.now()
                        currency.save()

                        stats["updated"] += 1

                    except cls.DoesNotExist:
                        stats["failed"] += 1
                        stats["errors"].append(f"Currency not found: {code}")
                        logger.warning(f"Currency not found during rate update: {code}")
                    except Exception as e:
                        stats["failed"] += 1
                        stats["errors"].append(f"Error updating {code}: {str(e)}")
                        logger.error(f"Error updating exchange rate for {code}: {e}")

                logger.info(
                    f"Exchange rates updated: {stats['updated']} successful, {stats['failed']} failed"
                )
                return stats

        except Exception as e:
            logger.error(f"Failed to update exchange rates: {e}")
            raise

    class Meta:
        verbose_name = _("Currency")
        verbose_name_plural = _("Currencies")
        ordering = ["code"]
        db_table = "currencies"
        indexes = [
            models.Index(fields=["code"]),
            models.Index(fields=["name"]),
            models.Index(fields=["is_active", "code"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(exchange_rate__gt=0),
                name="currency_exchange_rate_positive",
            ),
        ]


class Category(BaseModel):
    """
    Hierarchical categorization system for financial transactions.

    Supports:
    - User-specific and system-wide categories
    - Parent-child relationships (unlimited depth)
    - Category type (income/expense) enforcement
    - Soft deletion via is_active flag
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # User ownership (null for system categories)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="categories",
        db_index=True,
        help_text=_("Owner of this category. Null for system categories."),
    )

    # Hierarchical relationship
    parent = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="children",
        db_index=True,
        help_text=_("Parent category for hierarchical organization."),
    )

    name = models.CharField(
        max_length=255,
        db_index=True,
        help_text=_("Category name (must be unique per user and type)"),
    )
    description = models.TextField(
        null=True, blank=True, help_text=_("Detailed description of the category")
    )
    category_type = models.CharField(
        max_length=20,
        choices=choices.TransactionType.choices,
        default=choices.TransactionType.EXPENSE,
        db_index=True,
        help_text=_("Type of transactions this category is for"),
    )

    is_system_category = models.BooleanField(
        default=False, help_text=_("Whether this is a system-created category")
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text=_("Whether this category is active for use"),
    )

    # Usage statistics
    transaction_count = models.PositiveIntegerField(
        default=0, help_text=_("Number of transactions using this category")
    )
    last_used_at = models.DateTimeField(
        null=True, blank=True, help_text=_("When this category was last used")
    )

    def __str__(self) -> str:
        """Human-readable representation."""
        prefix = "[System] " if self.is_system_category else ""
        return f"{prefix}{self.name} ({self.get_category_type_display()})"

    def clean(self) -> None:
        """
        Validate category fields and relationships.

        Raises:
            ValidationError: If validation fails
        """
        logger.debug(f"Validating category: {self.name}")

        # System category validation
        if self.is_system_category and self.user is not None:
            raise ValidationError(
                {"user": _("System categories cannot have a user assigned.")}
            )

        # Manual validation for unique constraint with NULL user
        if self.user:
            # For user categories, ensure uniqueness
            existing = (
                Category.objects.filter(
                    user=self.user, name=self.name, category_type=self.category_type
                )
                .exclude(pk=self.pk)
                .first()
            )

            if existing:
                raise ValidationError(
                    {
                        "name": _(
                            f"A category with name '{self.name}' and type "
                            f"'{self.category_type}' already exists for this user."
                        )
                    }
                )
        else:
            # For system categories, ensure uniqueness
            existing = (
                Category.objects.filter(
                    user__isnull=True,
                    name=self.name,
                    category_type=self.category_type,
                    is_system_category=True,
                )
                .exclude(pk=self.pk)
                .first()
            )

            if existing:
                raise ValidationError(
                    {
                        "name": _(
                            f"A system category with name '{self.name}' and type "
                            f"'{self.category_type}' already exists."
                        )
                    }
                )

        # Parent validation
        if self.parent:
            # Prevent circular relationships
            if self.parent.id == self.id:
                raise ValidationError(
                    {"parent": _("Category cannot be its own parent.")}
                )

            # Check for circular references up the chain
            ancestors = self.get_ancestors()
            if self in ancestors:
                raise ValidationError(
                    {"parent": _("Circular reference detected in category hierarchy.")}
                )

            # Ensure parent has same type (optional business rule)
            if self.parent.category_type != self.category_type:
                logger.warning(
                    f"Category type mismatch: {self.category_type} vs parent {self.parent.category_type}"
                )

        # Name validation
        if not self.name or len(self.name.strip()) == 0:
            raise ValidationError({"name": _("Category name cannot be empty.")})

        logger.debug(f"Category validation passed: {self.name}")

    def get_ancestors(self, include_self: bool = False) -> List["Category"]:
        """
        Get all ancestor categories.

        Args:
            include_self: Whether to include current category

        Returns:
            List of ancestor categories
        """
        ancestors = []
        visited = set()
        current = self if include_self else self.parent

        while current:
            # Check for circular reference
            if current.id in visited:
                break  # Break the loop to prevent infinite recursion

            visited.add(current.id)
            ancestors.append(current)
            current = current.parent

        return ancestors

    def get_descendants(self, include_self: bool = False) -> List["Category"]:
        """
        Get all descendant categories.

        Args:
            include_self: Whether to include current category

        Returns:
            List of descendant categories
        """
        descendants = []

        def collect_children(category: "Category"):
            for child in category.children.all():
                descendants.append(child)
                collect_children(child)

        if include_self:
            descendants.append(self)

        collect_children(self)
        return descendants

    @property
    def full_path(self) -> str:
        """Get full hierarchical path of the category."""
        ancestors = self.get_ancestors(include_self=True)
        return " → ".join(cat.name for cat in reversed(ancestors))

    @classmethod
    def get_or_create_system_category(
        cls,
        name: str,
        category_type: str,
        parent: Optional["Category"] = None,
        **kwargs,
    ) -> Tuple["Category", bool]:
        """
        Get or create a system category.

        Args:
            name: Category name
            category_type: Income or expense
            parent: Parent category (optional)
            **kwargs: Additional category fields

        Returns:
            Tuple of (category, created)
        """
        try:
            category = cls.objects.get(
                user=None,
                name=name,
                category_type=category_type,
                is_system_category=True,
            )
            return category, False
        except cls.DoesNotExist:
            category = cls.objects.create(
                user=None,
                name=name,
                category_type=category_type,
                parent=parent,
                is_system_category=True,
                **kwargs,
            )
            logger.info(f"Created system category: {name} ({category_type})")
            return category, True

    def update_usage_stats(self) -> None:
        """Update transaction count and last used timestamp."""
        try:
            from accounts.models import Transaction

            count = Transaction.objects.filter(
                category=self, status=choices.TransactionStatus.COMPLETED
            ).count()

            last_used = (
                Transaction.objects.filter(category=self)
                .order_by("-transaction_date")
                .values_list("transaction_date", flat=True)
                .first()
            )

            self.transaction_count = count
            self.last_used_at = last_used
            self.save(update_fields=["transaction_count", "last_used_at", "updated_at"])

        except Exception as e:
            logger.error(f"Error updating category stats for {self.id}: {e}")

    class Meta:
        verbose_name = _("Category")
        verbose_name_plural = _("Categories")
        db_table = "categories"
        indexes = [
            models.Index(
                fields=["user", "is_active"],
                name=utils.get_index_name("categories", ["user", "is_active"]),
            ),
            models.Index(
                fields=["category_type", "is_active"],
                name=utils.get_index_name("categories", ["category_type", "is_active"]),
            ),
            models.Index(
                fields=["parent", "is_active"],
                name=utils.get_index_name("categories", ["parent", "is_active"]),
            ),
            models.Index(
                fields=["user", "category_type", "is_active"],
                name=utils.get_index_name(
                    "categories", ["user", "category_type", "is_active"]
                ),
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "name", "category_type"],
                name=utils.get_index_name(
                    "categories", ["user", "name", "category_type"]
                ),
            ),
            models.CheckConstraint(
                condition=~models.Q(parent=models.F("id")),
                name=utils.get_index_name("categories", ["parent", "id"]),
            ),
        ]
