from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from accounts.models import Account, Transaction
from core.models import Category, Currency
from utils import choices


@pytest.mark.django_db
class TestAccountModel:
    """Test suite for Account model."""

    @pytest.fixture
    def user(self, django_user_model):
        return django_user_model.objects.create_user(
            email="test@example.com",
            password="password",
            first_name="Test",
            last_name="User",
        )

    @pytest.fixture
    def currency(self):
        return Currency.objects.create(
            code="USD", name="Dollar", symbol="$", exchange_rate=1.0
        )

    @pytest.fixture
    def account(self, user, currency):
        """Fixture for a standard account."""
        return Account.objects.create(
            user=user,
            currency=currency,
            name="Main Checking",
            account_type=choices.AccountType.CHECKING,
            initial_balance=Decimal("1000.00"),
            current_balance=Decimal("1500.00"),
        )

    def test_create_account(self, user, currency):
        """Test creating a valid account."""
        account = Account.objects.create(
            user=user,
            currency=currency,
            name="Main Checking",
            account_type=choices.AccountType.CHECKING,
            initial_balance=Decimal("1000.00"),
        )
        assert account.pk is not None
        assert account.user == user
        assert account.currency == currency
        assert account.name == "Main Checking"
        assert account.initial_balance == Decimal("1000.00")
        assert account.current_balance == Decimal("0.00")
        assert account.is_active is True
        assert account.is_primary is False
        assert account.institution_data == {}

    def test_account_str_representation(self, account):
        """Test the string representation of the account."""
        expected_str = f"Main Checking (USD) - {account.user}"
        assert str(account) == expected_str

    def test_primary_account_switching(self, user, currency):
        """Test that setting a new primary account unsets the old one."""
        acc1 = Account.objects.create(
            user=user, currency=currency, name="Acc 1", is_primary=True
        )
        assert acc1.is_primary is True

        acc2 = Account.objects.create(
            user=user, currency=currency, name="Acc 2", is_primary=True
        )

        acc1.refresh_from_db()
        assert acc2.is_primary is True
        assert acc1.is_primary is False

    def test_unique_account_name_per_user(self, account):
        """Test that account names must be unique for the same user."""
        with pytest.raises(IntegrityError):
            Account.objects.create(
                user=account.user,
                currency=account.currency,
                name="Main Checking",  # Same name
                account_type=choices.AccountType.SAVINGS,
            )

    def test_available_balance_property(self, account):
        """Test that the available_balance property returns the current_balance."""
        assert account.available_balance == account.current_balance
        assert account.available_balance == Decimal("1500.00")

    def test_currency_protection_on_delete(self, account):
        """Test that a currency cannot be deleted if an account uses it."""
        with pytest.raises(Exception):  # Django raises a ProtectedError
            account.currency.delete()


@pytest.mark.django_db
class TestTransactionModel:
    """Test suite for the Transaction model."""

    @pytest.fixture
    def user(self, django_user_model):
        return django_user_model.objects.create_user(
            email="test@example.com",
            password="password",
            first_name="Test",
            last_name="User",
        )

    @pytest.fixture
    def currency(self):
        return Currency.objects.create(code="USD", name="US Dollar", symbol="$")

    @pytest.fixture
    def account(self, user, currency):
        return Account.objects.create(
            user=user,
            name="Test Account",
            currency=currency,
            current_balance=Decimal("1000.00"),
        )

    @pytest.fixture
    def category(self, user):
        return Category.objects.create(
            user=user, name="Groceries", category_type=choices.TransactionType.EXPENSE
        )

    def test_create_transaction(self, user, account, category):
        """Test creating a valid transaction."""
        transaction = Transaction.objects.create(
            user=user,
            account=account,
            category=category,
            name="Weekly Groceries",
            transaction_type=choices.TransactionType.EXPENSE,
            amount=Decimal("150.75"),
            currency=account.currency.code,
        )
        assert transaction.id is not None
        assert transaction.user == user
        assert transaction.account == account
        assert transaction.category == category
        assert transaction.amount == Decimal("150.75")
        assert transaction.status == choices.TransactionStatus.PENDING
        assert transaction.transaction_date == date.today()
        assert not transaction.is_recurring
        assert not transaction.is_transfer
        assert transaction.tags == []
        assert transaction.attachments == []

    def test_transaction_str_representation(self, user, category):
        """Test the string representation of a transaction."""
        transaction = Transaction.objects.create(
            user=user,
            category=category,
            name="Salary",
            transaction_type=choices.TransactionType.INCOME,
            amount=Decimal("5000.00"),
            currency="USD",
        )
        expected_str = "Salary (Income) - 5000.00 USD"
        assert str(transaction) == expected_str

    def test_transaction_amount_must_be_positive(self, user, account, category):
        """Test that the transaction amount must be positive."""
        with pytest.raises(ValidationError) as excinfo:
            tx = Transaction(
                user=user,
                account=account,
                category=category,
                name="Invalid Transaction",
                transaction_type=choices.TransactionType.EXPENSE,
                amount=Decimal("-100.00"),
                currency=account.currency.code,
            )
            tx.clean()
        assert "Amount must be a positive value" in str(excinfo.value)

    def test_exchange_rate_must_be_positive(self, user, account, category):
        """Test that the exchange rate must be positive."""
        with pytest.raises(ValidationError) as excinfo:
            tx = Transaction(
                user=user,
                account=account,
                category=category,
                name="Invalid Exchange Rate",
                transaction_type=choices.TransactionType.EXPENSE,
                amount=Decimal("100.00"),
                currency=account.currency.code,
                exchange_rate=Decimal("-1.2"),
            )
            tx.clean()
        assert "Exchange rate must be positive" in str(excinfo.value)

    def test_original_amount_and_currency_required_for_conversion(
        self, user, account, category
    ):
        """
        Test that original amount/currency are required if exchange rate is not 1.
        """
        with pytest.raises(ValidationError) as excinfo:
            tx = Transaction(
                user=user,
                account=account,
                category=category,
                name="Conversion Error",
                transaction_type=choices.TransactionType.EXPENSE,
                amount=Decimal("120.00"),
                currency=account.currency.code,
                exchange_rate=Decimal("1.2"),
                original_amount=None,  # Missing original amount
                original_currency="EUR",
            )
            tx.clean()
        assert "Original amount and original currency must be set" in str(excinfo.value)

        with pytest.raises(ValidationError) as excinfo:
            tx = Transaction(
                user=user,
                account=account,
                category=category,
                name="Conversion Error",
                transaction_type=choices.TransactionType.EXPENSE,
                amount=Decimal("120.00"),
                currency=account.currency.code,
                exchange_rate=Decimal("1.2"),
                original_amount=Decimal("100.00"),
                original_currency=None,  # Missing original currency
            )
            tx.clean()
        assert "Original amount and original currency must be set" in str(excinfo.value)

    def test_category_protection_on_delete(self, user, account, category):
        """Test that a category cannot be deleted if a transaction uses it."""
        Transaction.objects.create(
            user=user,
            account=account,
            category=category,
            name="Test Transaction",
            transaction_type=choices.TransactionType.EXPENSE,
            amount=Decimal("50.00"),
            currency=account.currency.code,
        )
        with pytest.raises(Exception):  # Django raises a ProtectedError
            category.delete()
