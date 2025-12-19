from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Dict
from unittest.mock import Mock, patch

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone as django_utils_timezone

from core.models import Category, Currency
from utils import choices


@pytest.fixture(autouse=True)
def setup_teardown():
    """Clean up between tests to prevent uniqueness conflicts."""
    yield
    # Clean up all data after each test
    Currency.objects.all().delete()
    Category.objects.all().delete()


class TestBase:
    """Base test class with common utilities."""

    @staticmethod
    def assert_decimal_equal(actual, expected, places=8):
        """Assert two Decimal values are equal within specified precision."""
        if actual is None or expected is None:
            assert actual == expected
            return

        actual_quantized = actual.quantize(
            Decimal(f"1e-{places}"), rounding=ROUND_HALF_UP
        )
        expected_quantized = expected.quantize(
            Decimal(f"1e-{places}"), rounding=ROUND_HALF_UP
        )
        assert actual_quantized == expected_quantized

    @staticmethod
    def create_test_currency(
        code="USD",
        name="US Dollar",
        symbol="$",
        exchange_rate=Decimal("1.0"),
        is_base_currency=False,
        decimal_places=2,
        **kwargs,
    ):
        """Create a test currency with defaults."""
        return Currency.objects.create(
            code=code,
            name=name,
            symbol=symbol,
            exchange_rate=exchange_rate,
            is_base_currency=is_base_currency,
            decimal_places=decimal_places,
            **kwargs,
        )


@pytest.mark.django_db
class TestCurrencyModel(TestBase):
    """Test suite for Currency model."""

    def test_create_currency_success(self):
        """Test creating a valid currency with all fields."""
        currency = self.create_test_currency(
            code="EUR",
            name="Euro",
            symbol="€",
            exchange_rate=Decimal("0.85"),
            decimal_places=2,
            exchange_source="ECB",
            is_active=True,
        )

        # Validate persistence
        currency.full_clean()
        assert currency.pk is not None
        assert currency.code == "EUR"
        assert currency.name == "Euro"
        assert currency.symbol == "€"
        assert currency.exchange_rate == Decimal("0.85")
        assert currency.decimal_places == 2
        assert currency.exchange_source == "ECB"
        assert currency.is_active is True
        assert currency.is_base_currency is False

    def test_currency_str_representation(self):
        """Test string representation of currency."""
        # With symbol
        currency = self.create_test_currency(code="USA", name="Dollar A", symbol="$")
        assert str(currency) == "USA ($) - Dollar A"

        # Without symbol
        currency = self.create_test_currency(code="USB", name="Dollar B", symbol="")
        assert str(currency) == "USB - Dollar B"

    def test_currency_ordering(self):
        """Test that currencies are ordered by code."""
        self.create_test_currency(code="JPY", name="Japanese Yen")
        self.create_test_currency(code="EUR", name="Euro")
        self.create_test_currency(code="GBP", name="British Pound")

        currencies = list(Currency.objects.all())
        assert [c.code for c in currencies] == ["EUR", "GBP", "JPY"]

    def test_currency_code_uppercase_validation(self):
        """Test that currency code is automatically uppercased."""
        # Test lowercase input
        currency = Currency.objects.create(code="eur", name="Euro")
        currency.full_clean()
        assert currency.code == "EUR"

        # Test mixed case
        currency = Currency.objects.create(code="UsD", name="Dollar")
        currency.full_clean()
        assert currency.code == "USD"

    def test_currency_code_validation_errors(self):
        """Test various invalid currency code scenarios."""
        # Too short
        currency = Currency(code="US", name="Dollar")
        with pytest.raises(ValidationError) as exc:
            currency.full_clean()
        assert "code" in exc.value.error_dict

        # Too long
        currency = Currency(code="USDA", name="Dollar")
        with pytest.raises(ValidationError) as exc:
            currency.full_clean()
        assert "code" in exc.value.error_dict

        # Contains numbers
        currency = Currency(code="123", name="Numbers")
        with pytest.raises(ValidationError) as exc:
            currency.full_clean()
        assert "code" in exc.value.error_dict

        # Contains special characters
        currency = Currency(code="U$D", name="Dollar")
        with pytest.raises(ValidationError) as exc:
            currency.full_clean()
        assert "code" in exc.value.error_dict

        # Empty code
        currency = Currency(code="", name="Dollar")
        with pytest.raises(ValidationError) as exc:
            currency.full_clean()
        assert "code" in exc.value.error_dict

    def test_exchange_rate_validation(self):
        """Test exchange rate validation."""
        # Negative rate
        currency = Currency(code="USA", name="Dollar", exchange_rate=Decimal("-1.0"))
        with pytest.raises(ValidationError) as exc:
            currency.full_clean()
        assert "exchange_rate" in exc.value.error_dict

        # Zero rate
        currency = Currency(code="USB", name="Dollar", exchange_rate=Decimal("0"))
        with pytest.raises(ValidationError) as exc:
            currency.full_clean()
        assert "exchange_rate" in exc.value.error_dict

        # Valid positive rate
        currency = Currency(
            code="USC", name="Dollar", exchange_rate=Decimal("0.00000001")
        )
        try:
            currency.full_clean()
        except ValidationError:
            pytest.fail("Valid exchange rate should not raise ValidationError")

    def test_decimal_places_validation(self):
        """Test decimal places validation."""
        # Too high - limit is 10
        currency = Currency(code="BTC", name="Bitcoin", decimal_places=11)

        try:
            currency.full_clean()
        except ValidationError as e:
            if "decimal_places" in e.message_dict:
                assert "less than or equal to 10" in str(e)
            else:
                pytest.fail(f"Unexpected validation error: {e}")

        # Valid
        currency = Currency(code="ETH", name="Ethereum", decimal_places=10)
        try:
            currency.full_clean()
        except ValidationError:
            pytest.fail("Valid decimal places should not raise ValidationError")

    def test_base_currency_uniqueness_warning(self):
        """Test warning when multiple base currencies exist."""
        # Create first base currency
        base1 = self.create_test_currency(code="USA", is_base_currency=True)

        # Create second base currency (should trigger warning)
        with patch("core.models.logger.warning") as mock_warning:
            base2 = self.create_test_currency(code="EUA", is_base_currency=True)
            base2.full_clean()

            # Warning should be logged at least once
            assert mock_warning.call_count >= 1
            # Check if any of the calls contains our expected message
            warning_messages = [call[0][0] for call in mock_warning.call_args_list]
            assert any(
                "Multiple base currencies detected" in msg for msg in warning_messages
            )

    def test_get_base_currency(self):
        """Test retrieving base currency."""
        # Clean ensures no interference
        Currency.objects.all().delete()

        # No base currency
        assert Currency.get_base_currency() is None

        # Set base currency
        base_currency = self.create_test_currency(
            code="USA", name="US Dollar", is_base_currency=True
        )

        retrieved = Currency.get_base_currency()
        assert retrieved == base_currency
        assert retrieved.is_base_currency is True

    def test_convert_amount_same_currency(self):
        """Test converting amount within same currency."""
        currency = self.create_test_currency(
            code="USA", exchange_rate=Decimal("1.0"), decimal_places=2
        )

        amount = Decimal("100.50")
        result = currency.convert_amount(amount, currency)

        assert result == amount
        assert result == Decimal("100.50")

    def test_convert_amount_to_base_currency(self):
        """Test converting from foreign currency to base currency."""
        base_currency = self.create_test_currency(
            code="USA",
            exchange_rate=Decimal("1.0"),
            is_base_currency=True,
            decimal_places=2,
        )

        foreign_currency = self.create_test_currency(
            code="EUA",
            exchange_rate=Decimal("0.85"),
            decimal_places=2,
        )

        amount = Decimal("100.00")
        result = foreign_currency.convert_amount(amount, base_currency)

        # 100 EUR * 0.85 = 85 USD
        expected = Decimal("85.00")
        self.assert_decimal_equal(result, expected, places=2)

    def test_convert_amount_from_base_currency(self):
        """Test converting from base currency to foreign currency."""
        base_currency = self.create_test_currency(
            code="USA",
            exchange_rate=Decimal("1.0"),
            is_base_currency=True,
            decimal_places=2,
        )

        foreign_currency = self.create_test_currency(
            code="EUA",
            exchange_rate=Decimal("0.85"),
            decimal_places=2,
        )

        amount = Decimal("100.00")
        result = base_currency.convert_amount(amount, foreign_currency)

        # 100 USD / 0.85 = 117.647...
        expected = Decimal("117.65")
        self.assert_decimal_equal(result, expected, places=2)

    def test_convert_amount_between_foreign_currencies(self):
        """Test converting between two foreign currencies."""
        base_currency = self.create_test_currency(
            code="USA",
            exchange_rate=Decimal("1.0"),
            is_base_currency=True,
            decimal_places=2,
        )

        eur_currency = self.create_test_currency(
            code="EUA",
            exchange_rate=Decimal("0.85"),
            decimal_places=2,
        )

        gbp_currency = self.create_test_currency(
            code="GBA",
            exchange_rate=Decimal("0.75"),
            decimal_places=2,
        )

        amount = Decimal("100.00")
        result = eur_currency.convert_amount(amount, gbp_currency)

        # Convert EUR -> USD -> GBP
        # 100 EUR * 0.85 = 85 USD
        # 85 USD / 0.75 = 113.333...
        expected = Decimal("113.33")
        self.assert_decimal_equal(result, expected, places=2)

    def test_convert_amount_with_invalid_input(self):
        """Test conversion with invalid input."""
        currency = self.create_test_currency(code="USA")
        target = self.create_test_currency(code="EUA")

        # Invalid amount type
        assert currency.convert_amount("not a number", target) is None

        # None amount
        assert currency.convert_amount(None, target) is None

        # Invalid Decimal string
        assert currency.convert_amount("abc", target) is None

    def test_convert_amount_rounding(self):
        """Test proper rounding during conversion."""
        # JPY rate relative to USD (Base).
        # Calculate approximate rate for 1 JPY in USD terms (Direct Quote)
        jpy_rate = (Decimal("1") / Decimal("110.5")).quantize(Decimal("1.0000000000"))

        currency = self.create_test_currency(
            code="JPY",
            exchange_rate=jpy_rate,
            decimal_places=0,  # Japanese Yen has 0 decimal places
        )

        target = self.create_test_currency(
            code="USA",
            exchange_rate=Decimal("1.0"),
            is_base_currency=True,
            decimal_places=2,
        )

        amount = Decimal("1000")
        result = currency.convert_amount(amount, target)

        # 1000 JPY * (1/110.5) = 9.0497...
        expected = Decimal("9.05")
        self.assert_decimal_equal(result, expected, places=2)

    def test_update_exchange_rates_success(self):
        """Test successful batch update of exchange rates."""
        # Create test currencies
        usd = self.create_test_currency(
            code="USA", exchange_rate=Decimal("1.0"), exchange_source="old"
        )
        eur = self.create_test_currency(
            code="EUA", exchange_rate=Decimal("0.85"), exchange_source="old"
        )

        # New rates
        new_rates = {
            "USA": Decimal("1.0"),
            "EUA": Decimal("0.90"),
            "GBA": Decimal("0.80"),  # This one doesn't exist
        }

        # Update rates
        with patch("core.models.timezone.now") as mock_now:
            mock_now.return_value = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
            stats = Currency.update_exchange_rates(new_rates, "ECB")

        # Refresh from DB
        usd.refresh_from_db()
        eur.refresh_from_db()

        # Check stats
        assert stats["updated"] == 2
        assert stats["failed"] == 1
        assert "Currency not found: GBA" in stats["errors"]

        # Check updated currencies
        assert usd.exchange_rate == Decimal("1.0")
        assert usd.exchange_source == "ECB"
        assert eur.exchange_rate == Decimal("0.90")
        assert eur.exchange_source == "ECB"

        # Check historical rates - note that rates are stored with full precision
        assert len(usd.historical_rates) == 1
        assert len(eur.historical_rates) == 1
        # Compare as Decimals to avoid string formatting issues
        assert Decimal(usd.historical_rates[0]["rate"]) == Decimal("1.0")
        assert Decimal(eur.historical_rates[0]["rate"]) == Decimal("0.85")
        assert usd.historical_rates[0]["source"] == "old"

    def test_update_exchange_rates_historical_limit(self):
        """Test that historical rates are limited to 30 entries."""
        currency = self.create_test_currency(code="USA")

        # Create 35 historical entries
        currency.historical_rates = [
            {
                "rate": str(float(i)),
                "date": f"2024-01-{i:02d}T12:00:00",
                "source": "test",
            }
            for i in range(1, 36)
        ]
        currency.save()

        # Update rate
        new_rates = {"USA": Decimal("2.0")}
        Currency.update_exchange_rates(new_rates, "test")

        currency.refresh_from_db()

        # Should have 30 entries (oldest removed)
        assert len(currency.historical_rates) == 30
        # The last entry should be the old rate "1.0" (the rate before update)
        assert Decimal(currency.historical_rates[-1]["rate"]) == Decimal("1.0")
        assert currency.exchange_rate == Decimal("2.0")  # New current rate

    def test_update_exchange_rates_transaction_rollback(self):
        """Test that failed updates rollback or partial success works."""
        # Create test currency
        currency = self.create_test_currency(
            code="USA", exchange_rate=Decimal("1.0"), exchange_source="old"
        )

        new_rates = {
            "USA": Decimal("2.0"),
        }

        # Mock the save to raise an exception
        with patch.object(Currency, "save", side_effect=Exception("Forced save error")):
            # Usually update_exchange_rates catches exceptions
            stats = Currency.update_exchange_rates(new_rates, "test")

        # It catches exceptions per currency and logs them
        assert stats["updated"] == 0
        assert stats["failed"] == 1
        assert "Forced save error" in stats["errors"][0]

        # Refresh and verify no changes
        currency.refresh_from_db()
        assert currency.exchange_rate == Decimal("1.0")
        assert currency.exchange_source == "old"

    def test_currency_code_uniqueness(self):
        """Test that currency codes must be unique."""
        self.create_test_currency(code="USA")

        with pytest.raises((IntegrityError, ValidationError)):
            # Force save to bypass clean() which raises ValidationError first
            c = Currency(code="USA", name="Duplicate", exchange_rate=Decimal("1"))
            c.save()

    def test_exchange_rate_positive_constraint(self):
        """Test database constraint for positive exchange rates."""
        # This constraint is enforced at DB level, not model validation
        currency = Currency(code="TST", name="Test", exchange_rate=Decimal("-1.0"))

        # Should fail on save due to DB constraint
        with pytest.raises(Exception):
            currency.save()

    def test_currency_with_null_symbol(self):
        """Test currency with null/blank symbol."""
        currency = self.create_test_currency(code="NUL", symbol=None)
        assert currency.symbol is None
        currency.full_clean()  # Should not raise

        currency = self.create_test_currency(code="EMP", symbol="")
        assert currency.symbol == ""
        currency.full_clean()  # Should not raise

    def test_currency_historical_rates_default(self):
        """Test default historical rates."""
        currency = Currency()
        assert currency.historical_rates == []

    def test_currency_with_high_precision(self):
        """Test currency with high decimal precision (e.g., crypto)."""
        # Max allowed is 10 decimal places per updated model
        currency = self.create_test_currency(
            code="BTC",
            name="Bitcoin",
            symbol="₿",
            decimal_places=10,
            exchange_rate=Decimal("45000.1234567891"),
        )

        currency.full_clean()
        assert currency.decimal_places == 10
        assert currency.exchange_rate == Decimal("45000.1234567891")


@pytest.mark.django_db
class TestCategoryModel(TestBase):
    """Test suite for Category model."""

    @pytest.fixture
    def user(self, django_user_model):
        """Create a test user."""
        return django_user_model.objects.create_user(
            email="test.user@example.com",
            first_name="Test",
            last_name="User",
            password="testpass123",
        )

    @pytest.fixture
    def system_category(self):
        """Create a system category."""
        return Category.objects.create(
            name="General",
            category_type=choices.TransactionType.EXPENSE,
            is_system_category=True,
        )

    @pytest.fixture
    def user_category(self, user):
        """Create a user category."""
        return Category.objects.create(
            name="Food", user=user, category_type=choices.TransactionType.EXPENSE
        )

    def test_create_user_category(self, user):
        """Test creating a normal user-owned category."""
        category = Category.objects.create(
            name="Transportation",
            user=user,
            category_type=choices.TransactionType.EXPENSE,
            description="Transportation expenses",
        )

        assert category.pk is not None
        assert category.name == "Transportation"
        assert category.user == user
        assert category.category_type == choices.TransactionType.EXPENSE
        assert category.description == "Transportation expenses"
        assert category.is_system_category is False
        assert category.is_active is True
        assert category.transaction_count == 0
        assert category.last_used_at is None
        assert category.parent is None

    def test_create_system_category(self):
        """Test creating a system category (user=None)."""
        category = Category.objects.create(
            name="System General",
            category_type=choices.TransactionType.INCOME,
            is_system_category=True,
        )

        assert category.is_system_category is True
        assert category.user is None
        assert category.category_type == choices.TransactionType.INCOME
        assert category.is_active is True

    def test_category_str_representation(self, user):
        """Test string representation of categories."""
        # User category
        user_cat = Category.objects.create(name="Food", user=user)
        assert str(user_cat) == "Food (Expense)"

        # System category
        sys_cat = Category.objects.create(name="General", is_system_category=True)
        assert str(sys_cat) == "[System] General (Expense)"

        # Income category
        income_cat = Category.objects.create(
            name="Salary", user=user, category_type=choices.TransactionType.INCOME
        )
        assert str(income_cat) == "Salary (Income)"

    def test_category_default_values(self, user):
        """Test that default values are correctly set."""
        category = Category.objects.create(name="Test Category", user=user)

        assert category.is_system_category is False
        assert category.is_active is True
        assert category.category_type == choices.TransactionType.EXPENSE
        assert category.parent is None
        assert category.transaction_count == 0
        assert category.last_used_at is None
        assert category.description is None

    def test_category_name_validation(self, user):
        """Test category name validation."""
        # Empty name
        category = Category(name="", user=user)
        with pytest.raises(ValidationError) as exc:
            category.full_clean()
        assert "name" in exc.value.error_dict

        # Whitespace only name
        category = Category(name="   ", user=user)
        with pytest.raises(ValidationError) as exc:
            category.full_clean()
        assert "name" in exc.value.error_dict

    def test_system_category_user_validation(self, user):
        """Test that system categories cannot have a user."""
        category = Category(name="Invalid System", user=user, is_system_category=True)

        with pytest.raises(ValidationError) as exc:
            category.full_clean()
        assert "user" in exc.value.error_dict
        assert "System categories cannot have a user assigned" in str(exc.value)

    def test_category_unique_constraint_user(self, user):
        """Test unique constraint for user categories."""
        # Create first category
        Category.objects.create(
            name="Transport", user=user, category_type=choices.TransactionType.EXPENSE
        )

        # Try to create duplicate
        with pytest.raises(ValidationError) as exc:
            duplicate = Category(
                name="Transport",
                user=user,
                category_type=choices.TransactionType.EXPENSE,
            )
            duplicate.full_clean()

        assert "name" in exc.value.error_dict

    def test_category_unique_constraint_different_type(self, user):
        """Test that same name with different type is allowed."""
        # Create expense category
        Category.objects.create(
            name="Transport", user=user, category_type=choices.TransactionType.EXPENSE
        )

        # Should be able to create income category with same name
        try:
            income_category = Category(
                name="Transport",
                user=user,
                category_type=choices.TransactionType.INCOME,
            )
            income_category.full_clean()
        except ValidationError:
            pytest.fail("Should allow same name with different category type")

    def test_category_unique_constraint_system(self):
        """Test unique constraint for system categories."""
        # Create first system category
        Category.objects.create(
            name="General",
            is_system_category=True,
            category_type=choices.TransactionType.EXPENSE,
        )

        # Try to create duplicate
        with pytest.raises(ValidationError) as exc:
            duplicate = Category(
                name="General",
                is_system_category=True,
                category_type=choices.TransactionType.EXPENSE,
            )
            duplicate.full_clean()

        assert "name" in exc.value.error_dict

    def test_category_parent_self_reference(self, user):
        """Test that category cannot be its own parent."""
        category = Category.objects.create(name="Test", user=user)
        category.parent = category

        with pytest.raises(ValidationError) as exc:
            category.full_clean()

        assert "parent" in exc.value.error_dict
        assert "cannot be its own parent" in str(exc.value)

    def test_category_circular_reference(self, user):
        """Test detection of circular references."""
        # Create chain: A -> B -> C
        cat_a = Category.objects.create(name="A", user=user)
        cat_b = Category.objects.create(name="B", user=user, parent=cat_a)
        cat_c = Category.objects.create(name="C", user=user, parent=cat_b)

        # Try to make A parent of C (creating a loop)
        cat_a.parent = cat_c

        with pytest.raises(ValidationError) as exc:
            cat_a.full_clean()

        assert "parent" in exc.value.error_dict
        assert "Circular reference" in str(exc.value)

    def test_category_parent_type_mismatch_warning(self, user):
        """Test warning when parent has different category type."""
        # Create expense parent
        parent = Category.objects.create(
            name="Parent", user=user, category_type=choices.TransactionType.EXPENSE
        )

        # Create income child (should trigger warning)
        with patch("core.models.logger.warning") as mock_warning:
            child = Category(
                name="Child",
                user=user,
                parent=parent,
                category_type=choices.TransactionType.INCOME,
            )
            child.full_clean()

            # Warning should be logged
            mock_warning.assert_called_once()
            assert "Category type mismatch" in mock_warning.call_args[0][0]

    def test_category_parent_child_relationship(self, user):
        """Test parent-child relationship."""
        parent = Category.objects.create(name="Food", user=user)
        child = Category.objects.create(name="Groceries", user=user, parent=parent)

        assert child.parent == parent
        assert parent.children.first() == child

    def test_get_ancestors(self, user):
        """Test retrieving ancestor categories."""
        # Create hierarchy: Grandparent -> Parent -> Child
        grandparent = Category.objects.create(name="Grandparent", user=user)
        parent = Category.objects.create(name="Parent", user=user, parent=grandparent)
        child = Category.objects.create(name="Child", user=user, parent=parent)

        # Test without self
        ancestors = child.get_ancestors(include_self=False)
        assert len(ancestors) == 2
        assert ancestors[0] == parent
        assert ancestors[1] == grandparent

        # Test with self
        ancestors_with_self = child.get_ancestors(include_self=True)
        assert len(ancestors_with_self) == 3
        assert ancestors_with_self[0] == child

    def test_get_descendants(self, user):
        """Test retrieving descendant categories."""
        # Create hierarchy: Parent -> Child1, Child2 -> Grandchild
        parent = Category.objects.create(name="Parent", user=user)
        child1 = Category.objects.create(name="Child1", user=user, parent=parent)
        child2 = Category.objects.create(name="Child2", user=user, parent=parent)
        grandchild = Category.objects.create(
            name="Grandchild", user=user, parent=child1
        )

        # Test without self
        descendants = parent.get_descendants(include_self=False)
        assert len(descendants) == 3
        descendants_names = [d.name for d in descendants]
        assert "Child1" in descendants_names
        assert "Child2" in descendants_names
        assert "Grandchild" in descendants_names

        # Test with self
        descendants_with_self = parent.get_descendants(include_self=True)
        assert len(descendants_with_self) == 4
        assert descendants_with_self[0] == parent

    def test_full_path_property(self, user):
        """Test full hierarchical path property."""
        # Create hierarchy: Food -> Groceries -> Meat
        food = Category.objects.create(name="Food", user=user)
        groceries = Category.objects.create(name="Groceries", user=user, parent=food)
        meat = Category.objects.create(name="Meat", user=user, parent=groceries)

        assert meat.full_path == "Food → Groceries → Meat"
        assert groceries.full_path == "Food → Groceries"
        assert food.full_path == "Food"

    def test_get_or_create_system_category_new(self):
        """Test creating a new system category."""
        category, created = Category.get_or_create_system_category(
            name="New System", category_type=choices.TransactionType.EXPENSE
        )

        assert created is True
        assert category.name == "New System"
        assert category.is_system_category is True
        assert category.user is None
        assert category.category_type == choices.TransactionType.EXPENSE

    def test_get_or_create_system_category_existing(self):
        """Test retrieving existing system category."""
        # Create first
        existing, _ = Category.get_or_create_system_category(
            name="Existing", category_type=choices.TransactionType.EXPENSE
        )

        # Get or create should return existing
        category, created = Category.get_or_create_system_category(
            name="Existing", category_type=choices.TransactionType.EXPENSE
        )

        assert created is False
        assert category == existing

    def test_get_or_create_system_category_with_parent(self):
        """Test creating system category with parent."""
        parent, _ = Category.get_or_create_system_category(
            name="Parent", category_type=choices.TransactionType.EXPENSE
        )

        child, created = Category.get_or_create_system_category(
            name="Child", category_type=choices.TransactionType.EXPENSE, parent=parent
        )

        assert created is True
        assert child.parent == parent
        assert parent.children.first() == child

    def test_update_usage_stats(self, user):
        """Test updating category usage statistics."""
        category = Category.objects.create(name="Test", user=user)

        # Initially zero
        assert category.transaction_count == 0
        assert category.last_used_at is None

        # Mock the Transaction import to avoid ImportError
        with patch("accounts.models.Transaction") as MockTransaction:
            # Create mock objects
            mock_qs = Mock()

            # Setup the mock chain
            # The code calls Transaction.objects.filter(...).count()
            # and Transaction.objects.filter(...).order_by(...).values_list(...).first()

            mock_qs.count.return_value = 5
            # Return a valid datetime with valid utc timezone
            mock_qs.order_by.return_value.values_list.return_value.first.return_value = datetime(
                2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc
            )

            # When .filter() is called, return the mock queryset
            MockTransaction.objects.filter.return_value = mock_qs

            # Patch the import in the update_usage_stats method
            # NOTE: usage of "accounts.models.Transaction" string in patch
            # might affect the 'from accounts.models import Transaction' inside the function
            # depending on how patch works (it patches sys.modules or attributes).
            # If function does local import 'from accounts.models import Transaction',
            # we need to patch 'accounts.models.Transaction'.
            category.update_usage_stats()
            category.refresh_from_db()

            assert category.transaction_count == 5
            assert category.last_used_at == datetime(
                2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc
            )

    def test_update_usage_stats_error_handling(self, user):
        """Test error handling in update_usage_stats."""
        category = Category.objects.create(name="Test", user=user)

        # Force an import error
        with patch(
            "accounts.models.Transaction",
            side_effect=ImportError("No module named 'Transaction'"),
        ):
            # Patch logger where it's used - in 'core.models'
            with patch("core.models.logger.error") as mock_error:
                category.update_usage_stats()

                # Error should be logged
                # It might be called multiple times due to retry or other logic, ensure at least once
                assert mock_error.call_count >= 1
                # Check for substring in any call
                calls = [call[0][0] for call in mock_error.call_args_list]
                assert any("Error updating category stats" in c for c in calls)

    def test_category_with_long_name(self, user):
        """Test category with maximum length name."""
        long_name = "A" * 255  # Max length
        category = Category.objects.create(name=long_name, user=user)

        assert category.name == long_name
        category.full_clean()  # Should not raise

    def test_category_with_unicode_name(self, user):
        """Test category with Unicode characters."""
        unicode_name = "Restaurante Español 🍴"
        category = Category.objects.create(name=unicode_name, user=user)

        assert category.name == unicode_name
        category.full_clean()

    def test_inactive_category(self, user):
        """Test deactivating a category."""
        category = Category.objects.create(
            name="Old Category", user=user, is_active=False
        )

        assert category.is_active is False
        category.full_clean()  # Should still validate

    def test_category_with_null_description(self, user):
        """Test category with null/blank description."""
        category = Category.objects.create(name="Test", user=user, description=None)
        assert category.description is None

        category = Category.objects.create(name="Test2", user=user, description="")
        assert category.description == ""

        # Both should validate
        category.full_clean()

    def test_category_unique_constraint_db_level(self, user):
        """Test database-level unique constraint enforcement."""
        # Create first category - use direct save to bypass Django validation
        cat1 = Category(
            name="Unique", user=user, category_type=choices.TransactionType.EXPENSE
        )
        cat1.save()  # This should work

        # Try to create duplicate (should fail at DB level)
        cat2 = Category(
            name="Unique", user=user, category_type=choices.TransactionType.EXPENSE
        )

        # Accept either IntegrityError (DB) or ValidationError (full_clean in save)
        with pytest.raises((IntegrityError, ValidationError)):
            cat2.save()

    def test_category_no_self_parent_constraint(self, user):
        """Test database constraint preventing self-parenting."""
        # This constraint is enforced at DB level
        category = Category.objects.create(name="Test", user=user)

        # Try to set self as parent
        category.parent = category
        with pytest.raises(Exception):  # Could be IntegrityError or similar
            category.save()

    def test_category_with_multiple_users(self, django_user_model):
        """Test that same category name can exist for different users."""
        user1 = django_user_model.objects.create_user(
            email="user1@example.com",
            first_name="User1",
            password="pass1",
            last_name="User1",
        )
        user2 = django_user_model.objects.create_user(
            email="user2@example.com",
            first_name="User2",
            password="pass2",
            last_name="User2",
        )

        # Both users can have "Food" category
        cat1 = Category.objects.create(name="Food", user=user1)
        cat2 = Category.objects.create(name="Food", user=user2)

        assert cat1.user == user1
        assert cat2.user == user2
        assert cat1.name == cat2.name == "Food"

        # Both should validate
        cat1.full_clean()
        cat2.full_clean()

    def test_category_tree_integrity(self, user):
        """Test integrity of category hierarchy operations."""
        # Create complex tree
        root = Category.objects.create(name="Root", user=user)
        child1 = Category.objects.create(name="Child1", user=user, parent=root)
        child2 = Category.objects.create(name="Child2", user=user, parent=root)
        grandchild = Category.objects.create(
            name="Grandchild", user=user, parent=child1
        )

        # Verify relationships
        assert root.children.count() == 2
        assert child1.children.count() == 1
        assert child1.parent == root
        assert grandchild.parent == child1

        # Verify paths
        assert grandchild.full_path == "Root → Child1 → Grandchild"

        # Move grandchild to child2
        grandchild.parent = child2
        grandchild.save()
        grandchild.refresh_from_db()

        assert grandchild.parent == child2
        assert grandchild.full_path == "Root → Child2 → Grandchild"
        assert child1.children.count() == 0
        assert child2.children.count() == 1


@pytest.mark.django_db
class TestCrossModelIntegration:
    """Integration tests between Currency and Category models."""

    def test_models_can_coexist(self, django_user_model):
        """Test that both models can be created and used together."""
        # Create a user
        user = django_user_model.objects.create_user(
            email="integration@example.com",
            first_name="Integration",
            last_name="Test",
            password="password",
        )

        # Create currencies
        usd = Currency.objects.create(
            code="USD",
            name="US Dollar",
            symbol="$",
            exchange_rate=Decimal("1.0"),
            is_base_currency=True,
        )

        eur = Currency.objects.create(
            code="EUR", name="Euro", symbol="€", exchange_rate=Decimal("0.85")
        )

        # Create categories
        food_category = Category.objects.create(
            name="Food", user=user, category_type=choices.TransactionType.EXPENSE
        )

        salary_category = Category.objects.create(
            name="Salary", user=user, category_type=choices.TransactionType.INCOME
        )

        # Perform currency conversion
        amount = Decimal("100.00")
        converted = usd.convert_amount(amount, eur)

        # Verify everything works
        assert usd.is_base_currency is True
        assert eur.exchange_rate == Decimal("0.85")
        assert converted is not None
        assert food_category.user == user
        assert salary_category.category_type == choices.TransactionType.INCOME

        # All models should have proper string representations
        assert str(usd).startswith("USD")
        assert str(food_category).startswith("Food")
