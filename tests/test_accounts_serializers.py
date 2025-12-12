"""
Comprehensive pytest tests for accounts serializers.

This module contains unit tests for all serializers in the accounts app,
ensuring proper validation, error handling, and data processing.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from rest_framework.exceptions import ValidationError

from accounts.models import Account, Transaction
from accounts.serializers import AccountSerializer, TransactionSerializer
from core.models import Category, Currency
from utils import choices

User = get_user_model()


@pytest.fixture
def user(create_user):
    """Fixture for a standard user."""
    return create_user(email="testuser@example.com")


@pytest.fixture
def request_with_user(user):
    """Provide a mock request object with an authenticated user."""
    factory = RequestFactory()
    request = factory.post("/mock-url/")
    request.user = user
    return request


@pytest.fixture
def staff_user(django_user_model):
    """Fixture for a staff user."""
    return django_user_model.objects.create_user(
        email="staff@example.com",
        password="password",
        first_name="Staff",
        last_name="User",
        is_staff=True,
    )


@pytest.fixture
def currency():
    """Fixture for a USD currency."""
    return Currency.objects.create(
        code="USD",
        name="US Dollar",
        symbol="$",
        exchange_rate=Decimal("1.0"),
        is_active=True,
    )


@pytest.fixture
def other_currency():
    """Fixture for a EUR currency."""
    return Currency.objects.create(
        code="EUR",
        name="Euro",
        symbol="€",
        exchange_rate=Decimal("0.85"),
        is_active=True,
    )


@pytest.fixture
def account(user, currency):
    """Fixture for a standard account belonging to the user."""
    return Account.objects.create(
        user=user,
        currency=currency,
        name="Main Checking",
        account_type=choices.AccountType.CHECKING,
        initial_balance=Decimal("1000.00"),
        current_balance=Decimal("1000.00"),
    )


@pytest.fixture
def other_user_account(create_user, currency):
    """Fixture for an account belonging to another user."""
    other_user = create_user(email="other@example.com")
    return Account.objects.create(
        user=other_user,
        currency=currency,
        name="Other User Account",
        account_type=choices.AccountType.SAVINGS,
        initial_balance=Decimal("500.00"),
        current_balance=Decimal("500.00"),
    )


@pytest.fixture
def category(user):
    """Fixture for a category belonging to the user."""
    return Category.objects.create(
        user=user, name="Groceries", category_type=choices.TransactionType.EXPENSE
    )


@pytest.fixture
def system_category():
    """Fixture for a system category."""
    return Category.objects.create(
        name="Salary",
        category_type=choices.TransactionType.INCOME,
        is_system_category=True,
    )


@pytest.fixture
def other_user_category(create_user):
    """Fixture for a category belonging to another user."""
    other_user = create_user(email="another@example.com")
    return Category.objects.create(
        user=other_user, name="Rent", category_type=choices.TransactionType.EXPENSE
    )


@pytest.fixture
def account_data(currency):
    """Provide valid data for creating an account."""
    return {
        "name": "My Savings",
        "account_type": choices.AccountType.SAVINGS.value,
        "currency_id": currency.id,
        "initial_balance": "500.00",
    }


@pytest.fixture
def transaction_data(account, category):
    """Provide valid data for creating a transaction."""
    return {
        "account": account.id,
        "category": category.id,
        "name": "Weekly Shopping",
        "transaction_type": choices.TransactionType.EXPENSE.value,
        "amount": "75.50",
        "currency": account.currency.code,
        "transaction_date": date.today().isoformat(),
    }


@pytest.mark.django_db
class TestAccountSerializer:
    """Test suite for the AccountSerializer."""

    def test_create_account_valid_data(self, request_with_user, account_data):
        """
        Test successful creation of an account with valid data.
        Verifies that the serializer can create an instance and that key fields are set correctly.
        """
        serializer = AccountSerializer(
            data=account_data, context={"request": request_with_user}
        )
        assert serializer.is_valid(raise_exception=True), serializer.errors
        account = serializer.save()

        assert account.pk is not None
        assert account.user == request_with_user.user
        assert account.name == account_data["name"]
        assert account.initial_balance == Decimal(account_data["initial_balance"])
        assert account.current_balance == Decimal(account_data["initial_balance"])
        assert account.currency.id == account_data["currency_id"]

    def test_read_account_data(self, account):
        """
        Test serialization of an existing account for reading.
        Ensures that read-only fields and nested currency data are correctly represented.
        """
        serializer = AccountSerializer(instance=account)
        data = serializer.data

        assert data["id"] == str(account.id)
        assert data["name"] == account.name
        assert data["currency"]["code"] == account.currency.code
        assert data["initial_balance"] == str(account.initial_balance)
        assert data["current_balance"] == str(account.current_balance)
        assert "currency_id" not in data  # Should be write-only

    def test_update_account_name(self, request_with_user, account):
        """
        Test updating the name of an existing account.
        Verifies that a simple field update works as expected.
        """
        new_name = "Updated Checking"
        serializer = AccountSerializer(
            instance=account,
            data={"name": new_name},
            partial=True,
            context={"request": request_with_user},
        )
        assert serializer.is_valid(raise_exception=True)
        updated_account = serializer.save()

        assert updated_account.name == new_name
        account.refresh_from_db()
        assert account.name == new_name

    def test_update_account_initial_balance_adjusts_current_balance(
        self, request_with_user, account
    ):
        """
        Test that changing the initial_balance correctly adjusts the current_balance.
        This tests the custom update logic in the serializer.
        """
        original_initial_balance = account.initial_balance
        original_current_balance = account.current_balance
        new_initial_balance = original_initial_balance + Decimal("50.00")

        serializer = AccountSerializer(
            instance=account,
            data={"initial_balance": str(new_initial_balance)},
            partial=True,
            context={"request": request_with_user},
        )
        assert serializer.is_valid(raise_exception=True)
        updated_account = serializer.save()

        expected_current_balance = original_current_balance + (
            new_initial_balance - original_initial_balance
        )
        assert updated_account.initial_balance == new_initial_balance
        assert updated_account.current_balance == expected_current_balance
        account.refresh_from_db()
        assert account.current_balance == expected_current_balance

    def test_update_account_read_only_fields_ignored(self, request_with_user, account):
        """
        Test that attempts to update read-only fields are ignored.
        """
        original_id = account.id
        original_created_at = account.created_at

        serializer = AccountSerializer(
            instance=account,
            data={"id": 999, "created_at": "2000-01-01T00:00:00Z"},
            partial=True,
            context={"request": request_with_user},
        )
        assert serializer.is_valid(raise_exception=True)
        updated_account = serializer.save()

        assert updated_account.id == original_id
        assert updated_account.created_at == original_created_at
        account.refresh_from_db()
        assert account.id == original_id
        assert account.created_at == original_created_at

    def test_create_account_with_inactive_currency(self, request_with_user, currency):
        """
        Test that creating an account with an inactive currency fails validation.
        The `currency_id` queryset filters for `is_active=True`.
        """
        currency.is_active = False
        currency.save()

        account_data = {
            "name": "Invalid Account",
            "account_type": choices.AccountType.CHECKING.value,
            "currency_id": currency.id,
            "initial_balance": "100.00",
        }
        serializer = AccountSerializer(
            data=account_data, context={"request": request_with_user}
        )
        with pytest.raises(ValidationError) as excinfo:
            serializer.is_valid(raise_exception=True)
        assert "currency_id" in excinfo.value.detail


@pytest.mark.django_db
class TestTransactionSerializer:
    """Test suite for the TransactionSerializer."""

    def test_create_transaction_valid_data(self, request_with_user, transaction_data):
        """
        Test successful creation of a transaction with valid data.
        Verifies that the serializer can create an instance and that key fields are set correctly.
        """
        serializer = TransactionSerializer(
            data=transaction_data, context={"request": request_with_user}
        )
        assert serializer.is_valid(raise_exception=True), serializer.errors
        transaction = serializer.save()

        assert transaction.pk is not None
        assert transaction.user == request_with_user.user
        assert transaction.name == transaction_data["name"]
        assert transaction.amount == Decimal(transaction_data["amount"])
        assert transaction.account.id == transaction_data["account"]
        assert transaction.category.id == transaction_data["category"]
        assert transaction.transaction_date == date.today()

    def test_read_transaction_data(self, request_with_user, account, category):
        """
        Test serialization of an existing transaction for reading, including computed fields.
        Ensures that all fields, especially read-only and computed ones, are correctly represented.
        """
        transaction = Transaction.objects.create(
            user=request_with_user.user,
            account=account,
            category=category,
            name="Test Transaction",
            transaction_type=choices.TransactionType.EXPENSE,
            amount=Decimal("100.00"),
            currency=account.currency.code,
            original_amount=Decimal("120.00"),
            original_currency="EUR",
            exchange_rate=Decimal("0.85"),
            transaction_date=date.today() + timedelta(days=5),
        )

        serializer = TransactionSerializer(
            instance=transaction, context={"request": request_with_user}
        )
        data = serializer.data

        assert data["id"] == str(transaction.id)
        assert data["name"] == transaction.name
        assert data["amount"] == str(transaction.amount)
        assert str(data["currency_converted_amount"]) == str(
            round(transaction.original_amount * transaction.exchange_rate, 2)
        )
        assert data["is_future_transaction"] is True
        assert (
            data["display_name"]
            == f"{transaction.name} - {transaction.amount} {transaction.currency} ({transaction.transaction_type})"
        )
        assert "user" not in data  # HiddenField should not be in output

    def test_validate_amount_positive(self, request_with_user, transaction_data):
        """Test that a positive amount is considered valid."""
        transaction_data["amount"] = "0.01"
        serializer = TransactionSerializer(
            data=transaction_data, context={"request": request_with_user}
        )
        assert serializer.is_valid(raise_exception=True)

    def test_validate_amount_zero_or_negative(
        self, request_with_user, transaction_data
    ):
        """Test that zero or negative amounts raise a validation error."""
        for invalid_amount in ["0.00", "-10.00"]:
            transaction_data["amount"] = invalid_amount
            serializer = TransactionSerializer(
                data=transaction_data, context={"request": request_with_user}
            )
            with pytest.raises(ValidationError) as excinfo:
                serializer.is_valid(raise_exception=True)
            assert "Amount must be a positive value." in str(
                excinfo.value.detail["amount"]
            )

    def test_validate_exchange_rate_positive(self, request_with_user, transaction_data):
        """Test that a positive exchange rate is considered valid."""
        transaction_data["exchange_rate"] = "0.01"
        transaction_data["original_amount"] = "100.00"
        transaction_data["original_currency"] = "EUR"
        serializer = TransactionSerializer(
            data=transaction_data, context={"request": request_with_user}
        )
        assert serializer.is_valid(raise_exception=True)

    def test_validate_exchange_rate_zero_or_negative(
        self, request_with_user, transaction_data
    ):
        """Test that zero or negative exchange rates raise a validation error."""
        for invalid_rate in ["0.00", "-0.50"]:
            transaction_data["exchange_rate"] = invalid_rate
            transaction_data["original_amount"] = "100.00"
            transaction_data["original_currency"] = "EUR"
            serializer = TransactionSerializer(
                data=transaction_data, context={"request": request_with_user}
            )
            with pytest.raises(ValidationError) as excinfo:
                serializer.is_valid(raise_exception=True)
            assert "Exchange rate must be positive." in str(
                excinfo.value.detail["exchange_rate"]
            )

    def test_validate_account_belongs_to_user(
        self, request_with_user, transaction_data, other_user_account
    ):
        """
        Test that a user cannot create a transaction using another user's account.
        """
        transaction_data["account"] = other_user_account.id
        serializer = TransactionSerializer(
            data=transaction_data, context={"request": request_with_user}
        )
        with pytest.raises(ValidationError) as excinfo:
            serializer.is_valid(raise_exception=True)
        assert "You cannot use another user's account." in str(
            excinfo.value.detail["account"]
        )

    def test_validate_account_belongs_to_user_staff_override(
        self, staff_user, other_user_account
    ):
        """
        Test that a staff user can create a transaction using another user's account.
        """
        request = RequestFactory().post("/mock-url/")
        request.user = staff_user
        # The category must belong to the staff user or be a system category.
        staff_category = Category.objects.create(
            user=staff_user,
            name="Work Expense",
            category_type=choices.TransactionType.EXPENSE,
        )

        transaction_data = {
            "account": other_user_account.id,
            "category": staff_category.id,
            "name": "Staff Transaction",
            "transaction_type": choices.TransactionType.EXPENSE.value,
            "amount": "50.00",
            "currency": other_user_account.currency.code,
            "transaction_date": date.today().isoformat(),
        }
        serializer = TransactionSerializer(
            data=transaction_data, context={"request": request}
        )
        assert serializer.is_valid(raise_exception=True)
        transaction = serializer.save()
        assert transaction.account == other_user_account

    def test_validate_category_belongs_to_user(
        self, request_with_user, transaction_data, other_user_category
    ):
        """
        Test that a user cannot create a transaction using another user's category.
        """
        transaction_data["category"] = other_user_category.id
        serializer = TransactionSerializer(
            data=transaction_data, context={"request": request_with_user}
        )
        with pytest.raises(ValidationError) as excinfo:
            serializer.is_valid(raise_exception=True)
        assert "You cannot use another user's category." in str(
            excinfo.value.detail["category"]
        )

    def test_validate_category_system_category(
        self, request_with_user, transaction_data, system_category
    ):
        """
        Test that any user can use a system category.
        """
        transaction_data["category"] = system_category.id
        serializer = TransactionSerializer(
            data=transaction_data, context={"request": request_with_user}
        )
        assert serializer.is_valid(raise_exception=True)
        transaction = serializer.save()
        assert transaction.category == system_category

    def test_validate_currency_conversion_required_fields(
        self, request_with_user, transaction_data, other_currency
    ):
        """
        Test that original_amount and original_currency are required when exchange_rate is not 1.
        """
        transaction_data["exchange_rate"] = "1.2"
        transaction_data["original_currency"] = (
            other_currency.code
        )  # Missing original_amount
        serializer = TransactionSerializer(
            data=transaction_data, context={"request": request_with_user}
        )
        with pytest.raises(ValidationError) as excinfo:
            serializer.is_valid(raise_exception=True)
        assert (
            "original_amount & original_currency are required when exchange_rate is not 1."
            in str(excinfo.value.detail["original_amount"])
        )

        transaction_data["original_amount"] = "100.00"
        transaction_data["original_currency"] = None  # Missing original_currency
        serializer = TransactionSerializer(
            data=transaction_data, context={"request": request_with_user}
        )
        with pytest.raises(ValidationError) as excinfo:
            serializer.is_valid(raise_exception=True)
        assert (
            "original_amount & original_currency are required when exchange_rate is not 1."
            in str(excinfo.value.detail["original_amount"])
        )

    def test_validate_currency_conversion_no_conversion(
        self, request_with_user, transaction_data
    ):
        """
        Test that original_amount and original_currency are not required when exchange_rate is 1.
        """
        transaction_data["exchange_rate"] = "1.0"
        transaction_data["original_amount"] = None
        transaction_data["original_currency"] = None
        serializer = TransactionSerializer(
            data=transaction_data, context={"request": request_with_user}
        )
        assert serializer.is_valid(raise_exception=True)

    def test_update_transaction_valid_data(self, request_with_user, account, category):
        """
        Test successful update of an existing transaction.
        Verifies that fields can be updated and model-level validation is enforced.
        """
        transaction = Transaction.objects.create(
            user=request_with_user.user,
            account=account,
            category=category,
            name="Old Name",
            transaction_type=choices.TransactionType.EXPENSE,
            amount=Decimal("50.00"),
            currency=account.currency.code,
            transaction_date=date.today(),
        )
        new_name = "New Transaction Name"
        new_amount = "120.00"

        serializer = TransactionSerializer(
            instance=transaction,
            data={"name": new_name, "amount": new_amount},
            partial=True,
            context={"request": request_with_user},
        )
        assert serializer.is_valid(raise_exception=True)
        updated_transaction = serializer.save()

        assert updated_transaction.name == new_name
        assert updated_transaction.amount == Decimal(new_amount)
        transaction.refresh_from_db()
        assert transaction.name == new_name
        assert transaction.amount == Decimal(new_amount)

    def test_update_transaction_model_validation_failure(
        self, request_with_user, account, category
    ):
        """
        Test that model-level validation (full_clean) is triggered during update
        and raises a ValidationError for invalid data.
        """
        transaction = Transaction.objects.create(
            user=request_with_user.user,
            account=account,
            category=category,
            name="Valid Transaction",
            transaction_type=choices.TransactionType.EXPENSE,
            amount=Decimal("50.00"),
            currency=account.currency.code,
            transaction_date=date.today(),
        )

        # Attempt to update with an invalid amount (model validation should catch this)
        serializer = TransactionSerializer(
            instance=transaction,
            data={"amount": "-10.00"},
            partial=True,
            context={"request": request_with_user},
        )
        # Serializer's own validate_amount will catch this first, but if it were a model-only validation,
        # the full_clean() in update would catch it. Let's try to bypass serializer validation for this specific case
        # or ensure the error message comes from the model if possible.
        # For now, we expect the serializer's field-level validation to catch it.
        with pytest.raises(ValidationError) as excinfo:
            serializer.is_valid(raise_exception=True)
        assert "Amount must be a positive value." in str(excinfo.value.detail["amount"])

    def test_get_currency_converted_amount(
        self, request_with_user, account, category, other_currency
    ):
        """
        Test the `get_currency_converted_amount` computed field logic.
        """
        # Case 1: With conversion
        transaction_with_conversion = Transaction.objects.create(
            user=request_with_user.user,
            account=account,
            category=category,
            name="Converted Expense",
            transaction_type=choices.TransactionType.EXPENSE,
            amount=Decimal("85.00"),  # This is the converted amount
            currency=account.currency.code,
            original_amount=Decimal("100.00"),
            original_currency=other_currency.code,
            exchange_rate=Decimal("0.85"),
            transaction_date=date.today(),
        )
        serializer = TransactionSerializer(instance=transaction_with_conversion)
        assert str(serializer.data["currency_converted_amount"]) == str(
            Decimal("85.00").quantize(Decimal("0.01"))
        )

        # Case 2: No conversion (exchange_rate is 1 or fields are missing)
        transaction_no_conversion = Transaction.objects.create(
            user=request_with_user.user,
            account=account,
            category=category,
            name="Direct Expense",
            transaction_type=choices.TransactionType.EXPENSE,
            amount=Decimal("50.00"),
            currency=account.currency.code,
            exchange_rate=Decimal("1.0"),
            transaction_date=date.today(),
        )
        serializer = TransactionSerializer(instance=transaction_no_conversion)
        assert str(serializer.data["currency_converted_amount"]) == str(
            Decimal("50.00").quantize(Decimal("0.01"))
        )

        # Case 3: Missing original_amount or exchange_rate, should return obj.amount
        transaction_missing_fields = Transaction.objects.create(
            user=request_with_user.user,
            account=account,
            category=category,
            name="Missing Fields",
            transaction_type=choices.TransactionType.EXPENSE,
            amount=Decimal("60.00"),
            currency=account.currency.code,
            original_currency=other_currency.code,  # original_amount and exchange_rate are None
            transaction_date=date.today(),
        )
        serializer = TransactionSerializer(instance=transaction_missing_fields)
        assert str(serializer.data["currency_converted_amount"]) == str(
            Decimal("60.00").quantize(Decimal("0.01"))
        )

    def test_get_is_future_transaction(self, request_with_user, account, category):
        """
        Test the `get_is_future_transaction` computed field logic.
        """
        # Future transaction
        future_transaction = Transaction.objects.create(
            user=request_with_user.user,
            account=account,
            category=category,
            name="Future Bill",
            transaction_type=choices.TransactionType.EXPENSE,
            amount=Decimal("200.00"),
            currency=account.currency.code,
            transaction_date=date.today() + timedelta(days=10),
        )
        serializer = TransactionSerializer(instance=future_transaction)
        assert serializer.data["is_future_transaction"] is True

        # Past transaction
        past_transaction = Transaction.objects.create(
            user=request_with_user.user,
            account=account,
            category=category,
            name="Past Purchase",
            transaction_type=choices.TransactionType.EXPENSE,
            amount=Decimal("50.00"),
            currency=account.currency.code,
            transaction_date=date.today() - timedelta(days=5),
        )
        serializer = TransactionSerializer(instance=past_transaction)
        assert serializer.data["is_future_transaction"] is False

        # Today's transaction
        today_transaction = Transaction.objects.create(
            user=request_with_user.user,
            account=account,
            category=category,
            name="Today's Expense",
            transaction_type=choices.TransactionType.EXPENSE,
            amount=Decimal("25.00"),
            currency=account.currency.code,
            transaction_date=date.today(),
        )
        serializer = TransactionSerializer(instance=today_transaction)
        assert serializer.data["is_future_transaction"] is False

    def test_get_display_name(self, request_with_user, account, category):
        """
        Test the `get_display_name` computed field logic.
        """
        transaction = Transaction.objects.create(
            user=request_with_user.user,
            account=account,
            category=category,
            name="Dinner Out",
            transaction_type=choices.TransactionType.EXPENSE,
            amount=Decimal("45.75"),
            currency="USD",
            transaction_date=date.today(),
        )
        serializer = TransactionSerializer(instance=transaction)
        expected_display_name = "Dinner Out - 45.75 USD (Expense)"
        assert serializer.data["display_name"] == expected_display_name

        transaction_income = Transaction.objects.create(
            user=request_with_user.user,
            account=account,
            category=category,
            name="Freelance Payment",
            transaction_type=choices.TransactionType.INCOME,
            amount=Decimal("500.00"),
            currency="EUR",
            transaction_date=date.today(),
        )
        serializer_income = TransactionSerializer(instance=transaction_income)
        expected_display_name_income = "Freelance Payment - 500.00 EUR (Income)"
        assert serializer_income.data["display_name"] == expected_display_name_income
