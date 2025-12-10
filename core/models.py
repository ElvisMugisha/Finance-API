from django.db import models, IntegrityError
from django.conf import settings
from decimal import Decimal, InvalidOperation
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from django.utils.timezone import now

from utils import loggings

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
        return f"{self.code} | {self.name} ({self.symbol or ''})".strip()

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
