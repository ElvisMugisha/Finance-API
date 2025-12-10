import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _

from core.models import Currency
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
