"""
Comprehensive pytest tests for accounts serializers.

This module contains unit tests for all serializers in the accounts app,
ensuring proper validation, error handling, and data processing.
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from accounts.models import Account, Transaction
from accounts.serializers import (
    AccountCreateSerializer,
    AccountDetailSerializer,
    AccountListSerializer,
    AccountReconcileSerializer,
    AccountSerializer,
    AccountUpdateSerializer,
    TransactionBulkCreateSerializer,
    TransactionCreateSerializer,
    TransactionSerializer,
    TransactionUpdateSerializer,
    TransactionVerificationSerializer,
    get_account_serializer,
)
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
        assert "formatted_balance" in data
        assert "available_balance" in data
        assert "can_be_primary" in data

    def test_validate_name_unique_per_user(self, request_with_user, account):
        """Test that account names must be unique per user."""
        data = {
            "name": account.name,  # Duplicate name
            "account_type": choices.AccountType.CHECKING,
            "currency_id": account.currency.id,
            "initial_balance": "100.00",
        }
        serializer = AccountSerializer(
            data=data, context={"request": request_with_user}
        )
        with pytest.raises(ValidationError) as excinfo:
            serializer.is_valid(raise_exception=True)
        assert "An account with this name already exists." in str(excinfo.value)

    def test_validate_name_empty(self, request_with_user, account_data):
        """Test name validation for empty strings."""
        account_data["name"] = "   "
        serializer = AccountSerializer(
            data=account_data, context={"request": request_with_user}
        )
        with pytest.raises(ValidationError) as excinfo:
            print(serializer.is_valid(raise_exception=True))
        # DRF catches "   " as "This field may not be blank." due to trim_whitespace=True and allow_blank=False
        assert "This field may not be blank." in str(excinfo.value)

    def test_validate_account_type(self, request_with_user, account_data):
        """Test validation for invalid account type."""
        account_data["account_type"] = "INVALID_TYPE"
        serializer = AccountSerializer(
            data=account_data, context={"request": request_with_user}
        )
        with pytest.raises(ValidationError) as excinfo:
            serializer.is_valid(raise_exception=True)
        assert '"INVALID_TYPE" is not a valid choice.' in str(excinfo.value)

    def test_primary_account_logic(self, request_with_user, account, currency):
        """Test that setting a new primary account demotes the old one."""
        account.is_primary = True
        account.save()

        new_account_data = {
            "name": "New Primary",
            "account_type": choices.AccountType.CHECKING,
            "currency_id": currency.id,
            "initial_balance": "100.00",
            "is_primary": True,
        }
        serializer = AccountSerializer(
            data=new_account_data, context={"request": request_with_user}
        )
        assert serializer.is_valid(raise_exception=True)
        new_account = serializer.save()

        account.refresh_from_db()
        assert new_account.is_primary is True
        assert account.is_primary is False

    def test_bank_details_validation(self, request_with_user, account_data):
        """Test validation warnings (logged) or stripping for bank details."""
        # Cash/Wallet should strip bank details
        account_data["account_type"] = choices.AccountType.CASH
        account_data["account_number"] = "123456"
        account_data["bank_name"] = "Test Bank"

        serializer = AccountSerializer(
            data=account_data, context={"request": request_with_user}
        )
        assert serializer.is_valid(raise_exception=True)
        # Note: The stripping happens in `validate` which modifies attrs.
        # But saving it will persist those modifications.
        acct = serializer.save()
        assert acct.account_number is None
        assert acct.bank_name is None


@pytest.mark.django_db
class TestAccountListSerializer:
    """Test suite for AccountListSerializer."""

    def test_serialization(self, account):
        """Test that list serializer returns correct subset of fields."""
        serializer = AccountListSerializer(instance=account)
        data = serializer.data

        expected_fields = {
            "id",
            "name",
            "account_type",
            "currency_code",
            "currency_symbol",
            "current_balance",
            "formatted_balance",
            "is_primary",
            "is_active",
            "is_locked",
            "can_transact",
            "created_at",
        }
        assert set(data.keys()) == expected_fields
        assert data["can_transact"] is True

    def test_can_transact_logic(self, account):
        """Test can_transact property."""
        account.is_locked = True
        account.save()
        serializer = AccountListSerializer(instance=account)
        assert serializer.data["can_transact"] is False


@pytest.mark.django_db
class TestAccountDetailSerializer:
    """Test suite for AccountDetailSerializer."""

    def test_serialization_with_stats(self, account, user, category):
        """Test detailed serialization including dynamic fields."""
        # Create income category
        income_category = Category.objects.create(
            user=user, name="Salary", category_type=choices.TransactionType.INCOME
        )

        # Create some transactions with proper names and matching categories
        Transaction.objects.create(
            user=user,
            account=account,
            category=income_category,
            name="Income Transaction",
            amount=Decimal("100.00"),
            transaction_type=choices.TransactionType.INCOME,
            status=choices.TransactionStatus.COMPLETED,
            transaction_date=date.today(),
        )
        Transaction.objects.create(
            user=user,
            account=account,
            category=category,  # This is already EXPENSE type from fixture
            name="Expense Transaction",
            amount=Decimal("50.00"),
            transaction_type=choices.TransactionType.EXPENSE,
            status=choices.TransactionStatus.COMPLETED,
            transaction_date=date.today(),
        )

        serializer = AccountDetailSerializer(instance=account)
        data = serializer.data

        assert "recent_transactions" in data
        assert len(data["recent_transactions"]) == 2
        assert "balance_trend" in data
        assert "transaction_stats" in data

        stats = data["transaction_stats"]
        assert stats["total_transactions"] == 2
        assert stats["total_income"] == "100.00"
        assert stats["total_expense"] == "50.00"
        assert stats["net_flow"] == "50.00"


@pytest.mark.django_db
class TestAccountCreateSerializer:
    """Test suite for AccountCreateSerializer."""

    def test_initial_balance_defaults_to_zero(self, request_with_user, currency):
        """Test that initial_balance defaults to 0.00 if missing."""
        data = {
            "name": "Zero Balance Account",
            "account_type": choices.AccountType.CHECKING,
            "currency_id": currency.id,
        }
        serializer = AccountCreateSerializer(
            data=data, context={"request": request_with_user}
        )
        assert serializer.is_valid(raise_exception=True)
        acct = serializer.save()
        assert acct.initial_balance == Decimal("0.00")
        assert acct.current_balance == Decimal("0.00")


@pytest.mark.django_db
class TestAccountUpdateSerializer:
    """Test suite for AccountUpdateSerializer."""

    def test_prevent_currency_change(self, request_with_user, account, other_currency):
        """Test that currency cannot be changed after creation."""
        data = {"currency_id": other_currency.id}
        serializer = AccountUpdateSerializer(
            instance=account,
            data=data,
            partial=True,
            context={"request": request_with_user},
        )
        with pytest.raises(ValidationError) as excinfo:
            serializer.is_valid(raise_exception=True)
        assert "Cannot change account currency" in str(excinfo.value)

    def test_prevent_account_type_change(self, request_with_user, account):
        """Test that account type cannot be changed after creation."""
        new_type = (
            choices.AccountType.SAVINGS
            if account.account_type == choices.AccountType.CHECKING
            else choices.AccountType.CHECKING
        )

        data = {"account_type": new_type}
        serializer = AccountUpdateSerializer(
            instance=account,
            data=data,
            partial=True,
            context={"request": request_with_user},
        )
        with pytest.raises(ValidationError) as excinfo:
            serializer.is_valid(raise_exception=True)
        assert "Cannot change account type" in str(excinfo.value)


@pytest.mark.django_db
class TestAccountReconcileSerializer:
    """Test suite for AccountReconcileSerializer."""

    def test_reconcile_account(self, request_with_user, account):
        """Test reconciling an account."""
        data = {
            "reconciled_balance": "1000.00",
            "reconciliation_date": date.today(),
            "notes": "Monthly reconciliation",
        }
        serializer = AccountReconcileSerializer(
            data=data, context={"request": request_with_user, "account": account}
        )
        assert serializer.is_valid(raise_exception=True)
        result = serializer.save()

        assert result["account_id"] == str(account.id)
        assert result["reconciled_balance"] == "1000.00"
        assert result["difference"] == "0.00"

        account.refresh_from_db()
        assert account.reconciled_balance == Decimal("1000.00")

    def test_reconcile_with_difference(self, request_with_user, account):
        """Test reconciling where there is a difference."""
        account.current_balance = Decimal("900.00")
        account.save()

        data = {"reconciled_balance": "1000.00", "notes": "Finding missing money"}
        serializer = AccountReconcileSerializer(
            data=data, context={"request": request_with_user, "account": account}
        )
        assert serializer.is_valid(raise_exception=True)
        result = serializer.save()

        assert result["difference"] == "100.00"
        assert result["requires_adjustment"] is True
        assert "adjustment_suggestion" in result


def test_get_account_serializer():
    """Test the serializer factory function."""
    assert get_account_serializer("list") == AccountListSerializer
    assert get_account_serializer("retrieve") == AccountDetailSerializer
    assert get_account_serializer("create") == AccountCreateSerializer
    assert get_account_serializer("update") == AccountUpdateSerializer
    assert get_account_serializer("reconcile") == AccountReconcileSerializer
    assert get_account_serializer("unknown") == AccountSerializer


@pytest.mark.django_db
class TestTransactionSerializer:
    """Detailed tests for TransactionSerializer logic including validation and transfers."""

    def test_validate_transfer_rules(
        self, request_with_user, account, account_data, category
    ):
        """Test transfer validation rules."""
        # Create second account
        account_data["name"] = "Second Account"
        account2 = Account.objects.create(
            user=request_with_user.user,
            currency=account.currency,
            name="Second Account",
            account_type=choices.AccountType.CHECKING,
            initial_balance=Decimal("0"),
        )

        # Test failure: Transfer to same account
        data = {
            "account": account.id,
            "category": category.id,
            "name": "Bad Transfer",
            "transaction_type": choices.TransactionType.EXPENSE,
            "amount": "100.00",
            "transaction_date": date.today(),
            "is_transfer": True,
            "transfer_account": account.id,  # Same account
        }
        serializer = TransactionSerializer(
            data=data, context={"request": request_with_user}
        )
        with pytest.raises(ValidationError) as excinfo:
            serializer.is_valid(raise_exception=True)
        assert "Cannot transfer to the same account" in str(excinfo.value)

        # Test failure: Missing transfer_account
        data["transfer_account"] = None
        serializer = TransactionSerializer(
            data=data, context={"request": request_with_user}
        )
        with pytest.raises(ValidationError) as excinfo:
            serializer.is_valid(raise_exception=True)
        assert "Transfer account is required" in str(excinfo.value)

    def test_create_updates_account_balance(self, request_with_user, account, category):
        """Test that creating a completed transaction updates account balance."""
        initial_balance = account.current_balance
        amount = Decimal("50.00")

        data = {
            "account": account.id,
            "category": category.id,
            "name": "Expense",
            "transaction_type": choices.TransactionType.EXPENSE,
            "amount": str(amount),
            "transaction_date": date.today(),
            "status": choices.TransactionStatus.COMPLETED,
        }
        serializer = TransactionSerializer(
            data=data, context={"request": request_with_user}
        )
        assert serializer.is_valid(raise_exception=True)
        serializer.save()

        account.refresh_from_db()
        assert account.current_balance == Decimal("950")  # 1000 - 50

    def test_update_completed_transaction_updates_balance(
        self, request_with_user, account, category
    ):
        """Test that updating a transaction to completed status updates balance."""
        tx = Transaction.objects.create(
            user=request_with_user.user,
            account=account,
            category=category,
            name="Pending",
            transaction_type=choices.TransactionType.EXPENSE,
            amount=Decimal("50.00"),
            status=choices.TransactionStatus.PENDING,
        )
        initial_balance = account.current_balance  # 1000

        # Update to COMPLETED
        serializer = TransactionSerializer(
            instance=tx,
            data={"status": choices.TransactionStatus.COMPLETED},
            partial=True,
            context={"request": request_with_user},
        )
        assert serializer.is_valid(raise_exception=True)
        serializer.save()

        account.refresh_from_db()
        assert account.current_balance == initial_balance - Decimal("50.00")

    def test_category_type_mismatch(self, request_with_user, account, category):
        """Test validation error when category type does not match transaction type."""
        # category fixture is EXPENSE
        data = {
            "account": account.id,
            "category": category.id,
            "name": "Income using Expense Category",
            "transaction_type": choices.TransactionType.INCOME,  # Mismatch
            "amount": "100.00",
            "transaction_date": date.today(),
        }
        serializer = TransactionSerializer(
            data=data, context={"request": request_with_user}
        )
        with pytest.raises(ValidationError) as excinfo:
            serializer.is_valid(raise_exception=True)
        assert "Category type" in str(excinfo.value) and "does not match" in str(
            excinfo.value
        )


@pytest.mark.django_db
class TestTransactionCreateSerializer:
    """Test suite for TransactionCreateSerializer."""

    def test_required_fields(self, request_with_user):
        """Test that certain fields are required."""
        data = {}  # Empty
        serializer = TransactionCreateSerializer(
            data=data, context={"request": request_with_user}
        )
        assert not serializer.is_valid()
        assert "transaction_type" in serializer.errors
        assert "amount" in serializer.errors
        assert "transaction_date" in serializer.errors


@pytest.mark.django_db
class TestTransactionUpdateSerializer:
    """Test suite for TransactionUpdateSerializer."""

    def test_readonly_restrictions(self, request_with_user, account, category):
        """Test that transaction_type and is_transfer are read-only."""
        tx = Transaction.objects.create(
            user=request_with_user.user,
            account=account,
            category=category,
            name="Original",
            transaction_type=choices.TransactionType.EXPENSE,
            amount=Decimal("10.00"),
            is_transfer=False,
        )

        data = {"transaction_type": choices.TransactionType.INCOME, "is_transfer": True}
        serializer = TransactionUpdateSerializer(
            instance=tx, data=data, partial=True, context={"request": request_with_user}
        )
        assert serializer.is_valid(raise_exception=True)
        updated_tx = serializer.save()

        # Should NOT have changed
        assert updated_tx.transaction_type == choices.TransactionType.EXPENSE
        assert updated_tx.is_transfer is False


@pytest.mark.django_db
class TestTransactionBulkCreateSerializer:
    """Test suite for Bulk Create Serializer."""

    def test_bulk_create_structure(self, request_with_user, account, category):
        """Test structure validation for bulk create."""
        tx_data = {
            "account": account.id,
            "category": category.id,
            "name": "Bulk Item",
            "transaction_type": choices.TransactionType.EXPENSE,
            "amount": "10.00",
            "transaction_date": date.today(),
        }

        data = {"transactions": [tx_data, tx_data]}

        serializer = TransactionBulkCreateSerializer(
            data=data, context={"request": request_with_user}
        )
        assert serializer.is_valid(raise_exception=True)
        assert len(serializer.validated_data["transactions"]) == 2


@pytest.mark.django_db
class TestTransactionVerificationSerializer:
    """Test suite for verification serializer."""

    def test_verification_update(self, request_with_user, account, category):
        """Test verifying a transaction."""
        tx = Transaction.objects.create(
            user=request_with_user.user,
            account=account,
            category=category,
            name="Pending Tx",
            transaction_type=choices.TransactionType.EXPENSE,
            amount=Decimal("50.00"),
            status=choices.TransactionStatus.PENDING,
        )

        data = {"posted_date": date.today()}

        serializer = TransactionVerificationSerializer(
            instance=tx, data=data, context={"request": request_with_user}
        )
        assert serializer.is_valid(raise_exception=True)
        updated_tx = serializer.save()

        assert updated_tx.status == choices.TransactionStatus.COMPLETED
        assert updated_tx.posted_date == date.today()

        # Verify balance updated
        account.refresh_from_db()
        assert account.current_balance == Decimal("950.00")  # 1000 - 50
