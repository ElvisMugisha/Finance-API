from decimal import Decimal

import pytest

from core.models import Currency


@pytest.mark.django_db
class TestCurrencyModel:
    """Test suite for Currency model."""

    def test_create_currency(self):
        """Test creating a valid currency."""
        currency = Currency.objects.create(
            code="USD", name="US Dollar", symbol="$", exchange_rate=Decimal("1")
        )
        currency.full_clean()  # ensures clean() validation passes
        assert currency.pk is not None
        assert str(currency) == "USD ($)"
        assert currency.is_active is True

    def test_currency_code_upper(self):
        """Test that currency code is uppercased by clean()."""
        currency = Currency.objects.create(
            code="eur", name="Euro", symbol="€", exchange_rate=0.85
        )
        currency.full_clean()
        assert currency.code == "EUR"
