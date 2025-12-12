import uuid
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from utils import choices, loggings

# Initialize logger
logger = loggings.setup_logging()


class Currency(models.Model):
    """
    ISO 4217 currency catalog with full audit trail.
    """

    id = models.BigAutoField(primary_key=True, editable=False)
    code = models.CharField(max_length=3, unique=True, db_index=True)
    name = models.CharField(max_length=100, db_index=True)
    symbol = models.CharField(max_length=5, null=True, blank=True)
    exchange_rate = models.DecimalField(
        max_digits=18, decimal_places=6, default=Decimal("1")
    )
    exchange_source = models.CharField(max_length=50, null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        """Human readable representation."""
        return f"{self.code} ({self.symbol})" if self.symbol else f"{self.code}"

    def clean(self):
        """
        Validate basic field invariants:
          - code: exactly 3 alphabetic characters (stored uppercase)
          - exchange_rate: positive decimal
        Raises:
            ValidationError on invalid data.
        """
        logger.debug(f"Validating currency: {self.code}")

        # Code validation
        if not self.code or len(self.code) != 3 or not self.code.isalpha():
            logger.debug("Currency code validation failed: %r", self.code)
            raise ValidationError(
                {"code": _("Currency code must be exactly 3 alphabetic characters.")}
            )
        self.code = self.code.upper()

        # Exchange rate validation
        try:
            if self.exchange_rate is None or self.exchange_rate <= 0:
                logger.debug("Exchange rate validation failed: %r", self.exchange_rate)
                raise ValidationError(
                    {
                        "exchange_rate": _(
                            "Exchange rate must be a positive decimal value."
                        )
                    }
                )

        except (InvalidOperation, TypeError):
            logger.debug("Exchange rate invalid type: %r", self.exchange_rate)
            raise ValidationError(
                {"exchange_rate": _("Exchange rate must be a valid decimal.")}
            )

    class Meta:
        verbose_name = "Currency"
        verbose_name_plural = "Currencies"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["code"]),
            models.Index(fields=["name"]),
            models.Index(fields=["is_active"]),
        ]


class Category(models.Model):
    """
    Represents an income or expense category used for financial classification.

    Supports:
    - System-level predefined categories (shared across all users)
    - User-defined personal categories
    - Optional hierarchical nesting (parent → subcategories)

    Key Behaviors:
    - Category names are unique *per user* and per category type.
    - System categories have `user=None` and `is_system_category=True`.
    - Subcategories inherit logic but are fully independent objects.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Null user means the category is globally available (system-created)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="categories",
        db_index=True,  # improves filtering by user in multi-tenant APIs
        help_text=_("Owner of this category. Null indicates a system-wide category."),
    )

    # Optional hierarchical relationship
    parent = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subcategories",
        db_index=True,
        help_text=_("Optional parent category for hierarchical grouping."),
    )

    name = models.CharField(
        max_length=255,
        db_index=True,  # Improves searches and dropdown displays
        help_text=_("Category name. Must be unique per user and type."),
    )
    description = models.TextField(null=True, blank=True)
    category_type = models.CharField(
        max_length=20,
        choices=choices.TransactionType.choices,
        default=choices.TransactionType.EXPENSE,
        db_index=True,  # Filtering by type is extremely common
        help_text=_("Indicates whether this category is for income or expenses."),
    )

    is_system_category = models.BooleanField(default=False)
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text=_("Whether this category is currently active."),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Category"
        verbose_name_plural = "Categories"

        # Unique category name per (user, type)
        # System categories also satisfy this since user = NULL
        constraints = [
            models.UniqueConstraint(
                fields=["user", "name", "category_type"],
                name="uq_category_user_name_type",
            )
        ]

        # Database indexes for common filtering patterns
        indexes = [
            models.Index(fields=["user", "is_active"], name="idx_user_active"),
            models.Index(fields=["category_type", "is_active"], name="idx_type_active"),
            models.Index(fields=["parent"], name="idx_parent_category"),
        ]

    def __str__(self):
        parent = f" → {self.parent.name}" if self.parent else ""
        return f"{self.name}{parent} [{self.category_type}]"

    def clean(self):
        # Prevent system categories from accidentally having a user assigned
        if self.is_system_category and self.user is not None:
            raise ValidationError("System categories must have user = NULL.")
