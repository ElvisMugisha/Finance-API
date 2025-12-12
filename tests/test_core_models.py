from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from core.models import Category, Currency
from utils import choices


@pytest.mark.django_db
class TestCurrencyModel:
    """Test suite for Currency model."""

    def test_create_currency_success(self):
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
            code="eur", name="Euro", symbol="€", exchange_rate=Decimal("0.85")
        )
        currency.full_clean()
        assert currency.code == "EUR"

    def test_invalid_currency_code_raises(self):
        """Test that invalid currency codes raise ValidationError."""
        currency = Currency(code="US", name="Dollar")
        with pytest.raises(ValidationError):
            currency.full_clean()

        currency = Currency(code="123", name="Numbers")
        with pytest.raises(ValidationError):
            currency.full_clean()

    def test_negative_or_zero_exchange_rate_raises(self):
        """Test that negative or zero exchange rates raise ValidationError."""
        currency = Currency(code="USD", name="Dollar", exchange_rate=Decimal("-1"))
        with pytest.raises(ValidationError):
            currency.full_clean()

        currency = Currency(code="USD", name="Dollar", exchange_rate=Decimal("0"))
        with pytest.raises(ValidationError):
            currency.full_clean()


@pytest.mark.django_db
class TestCategoryModel:
    """Test suite for Category model."""

    @pytest.fixture
    def user(self, django_user_model):
        return django_user_model.objects.create_user(
            email="catuser@example.com",
            first_name="Cat",
            last_name="User",
            password="password",
        )

    def test_create_user_category(self, user):
        """Test creating a normal user-owned category."""
        cat = Category.objects.create(
            name="Food", user=user, category_type=choices.TransactionType.EXPENSE
        )
        assert cat.name == "Food"
        assert cat.user == user
        assert cat.category_type == choices.TransactionType.EXPENSE
        assert cat.is_system_category is False
        assert cat.is_active is True
        assert str(cat) == "Food [Expense]"

    def test_create_system_category(self):
        """Test creating a system category (user=None)."""
        cat = Category.objects.create(name="General", is_system_category=True)
        assert cat.is_system_category
        assert cat.user is None
        assert cat.category_type == choices.TransactionType.EXPENSE
        assert str(cat) == "General [Expense]"

    def test_category_with_parent(self, user):
        """Test hierarchical parent-child relationship."""
        parent = Category.objects.create(name="Food", user=user)
        child = Category.objects.create(name="Groceries", user=user, parent=parent)
        assert child.parent == parent
        assert str(child) == "Groceries → Food [Expense]"

    def test_unique_constraint_user_category(self, user):
        """Test that unique constraint per (user, name, category_type) is enforced."""
        Category.objects.create(name="Transport", user=user)
        with pytest.raises(IntegrityError):
            Category.objects.create(name="Transport", user=user)

    def test_system_category_user_not_allowed(self, django_user_model):
        """System categories cannot have a user assigned."""
        user = django_user_model.objects.create_user(
            email="syscat@example.com",
            first_name="Sys",
            last_name="User",
            password="password",
        )
        cat = Category(name="System Cat", user=user, is_system_category=True)
        with pytest.raises(ValidationError):
            cat.clean()

    def test_category_default_values(self, user):
        """Test that default values are correctly set."""
        cat = Category.objects.create(name="Bills", user=user)
        assert cat.is_system_category is False
        assert cat.is_active is True
        assert cat.category_type == choices.TransactionType.EXPENSE
        assert cat.parent is None
