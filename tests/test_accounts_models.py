from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db import transaction as db_transaction
from django.utils import timezone

from accounts.models import Account, Budget, BudgetCategory, FinancialGoal, Transaction
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
            code="USD",
            name="Dollar",
            symbol="$",
            exchange_rate=1.0,
            is_base_currency=True,
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
            current_balance=Decimal("1000.00"),
        )

    def test_create_account(self, user, currency):
        """Test creating a valid account."""
        account = Account.objects.create(
            user=user,
            currency=currency,
            name="Savings Account",
            account_type=choices.AccountType.SAVINGS,
            initial_balance=Decimal("500.00"),
            current_balance=Decimal("500.00"),
        )
        assert account.pk is not None
        assert account.user == user
        assert account.currency == currency
        assert account.name == "Savings Account"
        assert account.current_balance == Decimal("500.00")
        assert account.is_active is True

    def test_account_str_representation(self, account):
        """Test the string representation of the account."""
        expected_str = f"{account.name} (USD) - {account.user}"
        assert str(account) == expected_str

        account.is_primary = True
        account.save()
        expected_str_primary = f"★ {account.name} (USD) - {account.user}"
        assert str(account) == expected_str_primary

    def test_primary_account_switching(self, user, currency):
        """Test that setting a new primary account unsets the old one."""
        acc1 = Account.objects.create(
            user=user,
            currency=currency,
            name="Acc 1",
            is_primary=True,
            account_type=choices.AccountType.CHECKING,
        )
        assert acc1.is_primary is True

        acc2 = Account.objects.create(
            user=user,
            currency=currency,
            name="Acc 2",
            is_primary=True,
            account_type=choices.AccountType.SAVINGS,
        )

        acc1.refresh_from_db()
        assert acc2.is_primary is True
        assert acc1.is_primary is False

    def test_unique_account_name_per_user(self, account):
        """Test that account names must be unique for the same user."""
        with pytest.raises((IntegrityError, ValidationError)):
            Account.objects.create(
                user=account.user,
                currency=account.currency,
                name="Main Checking",  # Same name
                account_type=choices.AccountType.SAVINGS,
            )

    def test_clean_method_validates_bank_account(self, user, currency):
        """Test validation for bank accounts missing account number."""
        account = Account(
            user=user,
            currency=currency,
            name="Bank Account",
            account_type=choices.AccountType.BANK,
            # Missing account_number
        )
        # Should verify logger warning or just pass if it's only a warning logic in core
        # The model logs a warning but doesn't raise ValidationError for this specific case in clean()
        # strictly based on provided code.
        account.clean()  # Should not raise

    def test_clean_method_validates_balance(self, user, currency):
        """Test validation for extremely negative balance."""
        account = Account(
            user=user,
            currency=currency,
            name="Debt Account",
            account_type=choices.AccountType.CREDIT_CARD,
            current_balance=Decimal("-2000000.00"),
        )
        with pytest.raises(ValidationError) as excinfo:
            account.clean()
        assert "current_balance" in excinfo.value.message_dict

    def test_update_balance(self, account):
        """Test updating account balance."""
        # Test Income
        success = account.update_balance(
            Decimal("100.00"), choices.TransactionType.INCOME
        )
        assert success is True
        account.refresh_from_db()
        assert account.current_balance == Decimal("1100.00")

        # Test Expense
        success = account.update_balance(
            Decimal("50.00"), choices.TransactionType.EXPENSE
        )
        assert success is True
        account.refresh_from_db()
        assert account.current_balance == Decimal("1050.00")

        # Test Invalid Amount
        success = account.update_balance(
            Decimal("-10.00"), choices.TransactionType.INCOME
        )
        assert success is False

        # Test Invalid Type
        success = account.update_balance(Decimal("10.00"), "INVALID_TYPE")
        assert success is False

    def test_get_balance_history(self, account, user):
        """Test retrieving balance history."""
        # Create some transactions
        category = Category.objects.create(
            user=user, name="Income", category_type=choices.TransactionType.INCOME
        )
        Transaction.objects.create(
            user=user,
            account=account,
            category=category,
            name="Salary",
            transaction_type=choices.TransactionType.INCOME,
            amount=Decimal("1000.00"),
            status=choices.TransactionStatus.COMPLETED,
            transaction_date=timezone.now().date(),
        )

        history = account.get_balance_history()
        assert len(history) > 0
        assert history[-1]["balance"] == account.current_balance

    def test_available_balance(self, account):
        """Test available balance property."""
        assert account.available_balance == account.current_balance

    def test_formatted_balance(self, account):
        """Test formatted balance string."""
        # Assuming currency symbol is $
        assert account.formatted_balance == "$1000.00"

    def test_get_user_accounts_summary(self, user, account):
        """Test summary generation."""
        summary = Account.get_user_accounts_summary(user.id)
        assert summary["total_balance"] == Decimal("1000.00")
        assert summary["account_count"] == 1
        assert "USD" in summary["currency_balances"]


@pytest.mark.django_db
class TestTransactionModel:
    """Test suite for Transaction model."""

    @pytest.fixture
    def user(self, django_user_model):
        return django_user_model.objects.create_user(
            email="tx_user@example.com",
            password="password",
            first_name="Tx",
            last_name="User",
        )

    @pytest.fixture
    def currency(self):
        return Currency.objects.create(
            code="USD",
            name="Dollar",
            symbol="$",
            exchange_rate=1.0,
            is_base_currency=True,
        )

    @pytest.fixture
    def account(self, user, currency):
        return Account.objects.create(
            user=user,
            currency=currency,
            name="Tx Account",
            account_type=choices.AccountType.CHECKING,
            current_balance=Decimal("1000.00"),
        )

    @pytest.fixture
    def secondary_account(self, user, currency):
        return Account.objects.create(
            user=user,
            currency=currency,
            name="Savings",
            account_type=choices.AccountType.SAVINGS,
            current_balance=Decimal("500.00"),
        )

    @pytest.fixture
    def category(self, user):
        return Category.objects.create(
            user=user, name="Food", category_type=choices.TransactionType.EXPENSE
        )

    def test_create_transaction_updates_balance(self, user, account, category):
        """Test that creating a completed transaction updates account balance."""
        initial_balance = account.current_balance
        amount = Decimal("50.00")

        Transaction.objects.create(
            user=user,
            account=account,
            category=category,
            name="Lunch",
            transaction_type=choices.TransactionType.EXPENSE,
            amount=amount,
            status=choices.TransactionStatus.COMPLETED,
        )

        account.refresh_from_db()
        assert account.current_balance == initial_balance - amount

    def test_clean_validation(self, user, account, category):
        """Test transaction validation rules."""
        # Negative Amount
        tx = Transaction(
            user=user,
            account=account,
            category=category,
            name="Invalid",
            transaction_type=choices.TransactionType.EXPENSE,
            amount=Decimal("-10.00"),
        )
        with pytest.raises(ValidationError):
            tx.clean()

        # Category Type Mismatch
        income_cat = Category.objects.create(
            user=user, name="Salary", category_type=choices.TransactionType.INCOME
        )
        tx_mismatch = Transaction(
            user=user,
            account=account,
            category=income_cat,
            name="Mismatch",
            transaction_type=choices.TransactionType.EXPENSE,  # Mismatch
            amount=Decimal("10.00"),
        )
        with pytest.raises(ValidationError):
            tx_mismatch.clean()

    def test_transfer_logic(self, user, account, secondary_account, category):
        """Test transfer between accounts creates paired transaction."""
        transfer_amount = Decimal("100.00")

        tx = Transaction.objects.create(
            user=user,
            account=account,
            category=category,
            name="Transfer to Savings",
            transaction_type=choices.TransactionType.EXPENSE,
            amount=transfer_amount,
            status=choices.TransactionStatus.COMPLETED,
            is_transfer=True,
            transfer_account=secondary_account,
        )

        # Check source account balance
        account.refresh_from_db()
        assert account.current_balance == Decimal("900.00")  # 1000 - 100

        # Check destination account balance (via paired transaction)
        secondary_account.refresh_from_db()
        assert secondary_account.current_balance == Decimal("600.00")  # 500 + 100

        # Check paired transaction existence
        assert tx.transfer_reference is not None
        paired_tx = Transaction.objects.get(id=tx.transfer_reference)
        assert paired_tx.account == secondary_account
        assert paired_tx.transaction_type == choices.TransactionType.INCOME
        assert paired_tx.amount == transfer_amount

    def test_verify_method(self, user, account, category):
        """Test verifying a pending transaction."""
        tx = Transaction.objects.create(
            user=user,
            account=account,
            category=category,
            name="Pending Tx",
            transaction_type=choices.TransactionType.EXPENSE,
            amount=Decimal("50.00"),
            status=choices.TransactionStatus.PENDING,
        )

        initial_balance = account.current_balance
        assert tx.verify(user) is True

        tx.refresh_from_db()
        account.refresh_from_db()

        assert tx.status == choices.TransactionStatus.COMPLETED
        assert account.current_balance == initial_balance - Decimal("50.00")

    def test_reconcile_method(self, user, account, category):
        """Test reconciling a transaction."""
        tx = Transaction.objects.create(
            user=user,
            account=account,
            category=category,
            name="Tx",
            transaction_type=choices.TransactionType.EXPENSE,
            amount=Decimal("50.00"),
            status=choices.TransactionStatus.COMPLETED,
        )

        # Reconcile with same amount
        assert tx.reconcile() is True
        tx.refresh_from_db()
        assert tx.status == choices.TransactionStatus.RECONCILED

        # Reconcile with different amount
        new_amount = Decimal("55.00")
        assert tx.reconcile(reconciled_amount=new_amount) is True
        tx.refresh_from_db()
        assert tx.amount == new_amount

    def test_get_user_transactions_summary(self, user, account, category):
        """Test transaction summary."""
        Transaction.objects.create(
            user=user,
            account=account,
            category=category,
            name="T1",
            transaction_type=choices.TransactionType.EXPENSE,
            amount=Decimal("50.00"),
            status=choices.TransactionStatus.COMPLETED,
        )

        summary = Transaction.get_user_transactions_summary(user.id)
        assert summary["total_expense"] == Decimal("50.00")
        assert summary["transaction_count"] == 1


@pytest.mark.django_db
class TestBudgetModel:
    """Test suite for Budget model."""

    @pytest.fixture
    def user(self, django_user_model):
        return django_user_model.objects.create_user(
            email="budget_user@example.com",
            password="password",
            first_name="Budget",
            last_name="User",
        )

    @pytest.fixture
    def currency(self):
        return Currency.objects.create(
            code="USD", name="Dollar", symbol="$", exchange_rate=1.0
        )

    @pytest.fixture
    def category(self, user):
        return Category.objects.create(
            user=user, name="Groceries", category_type=choices.TransactionType.EXPENSE
        )

    @pytest.fixture
    def budget(self, user, category, currency):
        return Budget.objects.create(
            user=user,
            name="Grocery Budget",
            budget_type=choices.BudgetType.CATEGORY,
            category=category,
            total_budget=Decimal("500.00"),
            currency=currency,
            start_date=date.today(),
            end_date=date.today() + timedelta(days=30),
        )

    def test_create_budget(self, budget):
        """Test valid budget creation."""
        assert budget.pk is not None
        assert budget.total_remaining == Decimal("500.00")

    def test_budget_validation(self, user, currency, category):
        """Test budget validation rules."""
        # Invalid dates
        with pytest.raises(ValidationError):
            b = Budget(
                user=user,
                name="Bad Dates",
                budget_type=choices.BudgetType.CATEGORY,
                category=category,
                total_budget=Decimal("100.00"),
                currency=currency,
                start_date=date.today(),
                end_date=date.today() - timedelta(days=1),  # End before start
            )
            b.clean()

        # Negative Amount
        with pytest.raises(ValidationError):
            b = Budget(
                user=user,
                name="Bad Amount",
                budget_type=choices.BudgetType.CATEGORY,
                category=category,
                total_budget=Decimal("-100.00"),
                currency=currency,
                start_date=date.today(),
                end_date=date.today() + timedelta(days=1),
            )
            b.clean()

    def test_calculate_spending(self, budget, user, category):
        """Test spending calculation."""
        # Create an expense in the category
        account = Account.objects.create(
            user=user,
            currency=budget.currency,
            name="Cash",
            current_balance=Decimal("1000.00"),
        )
        Transaction.objects.create(
            user=user,
            account=account,
            category=category,
            name="Food",
            transaction_type=choices.TransactionType.EXPENSE,
            amount=Decimal("50.00"),
            status=choices.TransactionStatus.COMPLETED,
            transaction_date=budget.start_date,
        )

        spending = budget.calculate_spending()
        assert spending == Decimal("50.00")

        budget.refresh_from_db()
        assert budget.total_spent == Decimal("50.00")

    def test_advance_period(self, budget):
        """Test advancing budget period."""
        old_end_date = budget.end_date
        budget.rollover_unused = True
        budget.save()

        # Simulate some spending
        budget.total_spent = Decimal("100.00")
        budget.save()

        assert budget.advance_period() is True

        budget.refresh_from_db()
        assert budget.start_date == old_end_date + timedelta(days=1)
        assert budget.rollover_amount == Decimal("400.00")  # 500 - 100
        assert budget.total_spent == Decimal("0.00")

    def test_budget_category_junction(self, budget, user):
        """Test BudgetCategory junction model."""
        other_cat = Category.objects.create(
            user=user, name="Other", category_type=choices.TransactionType.EXPENSE
        )

        bc = BudgetCategory.objects.create(
            budget=budget, category=other_cat, allocated_amount=Decimal("100.00")
        )

        assert bc.remaining_amount == Decimal("100.00")

        # Test spending calculation on junction
        bc.calculate_spending()
        assert bc.spent_amount == Decimal("0.00")


@pytest.mark.django_db
class TestFinancialGoalModel:
    """Test suite for FinancialGoal model."""

    @pytest.fixture
    def user(self, django_user_model):
        return django_user_model.objects.create_user(
            email="goal_user@example.com",
            password="password",
            first_name="Goal",
            last_name="User",
        )

    @pytest.fixture
    def currency(self):
        return Currency.objects.create(
            code="USD", name="Dollar", symbol="$", exchange_rate=1.0
        )

    @pytest.fixture
    def goal(self, user, currency):
        return FinancialGoal.objects.create(
            user=user,
            name="New Car",
            target_amount=Decimal("20000.00"),
            currency=currency,
            target_date=date.today() + timedelta(days=365),
            monthly_contribution=Decimal("500.00"),
        )

    def test_create_goal(self, goal):
        """Test goal creation."""
        assert goal.pk is not None
        assert goal.progress_percentage == Decimal("0.00")
        assert goal.months_remaining > 0

    def test_goal_validation(self, user, currency):
        """Test goal validation."""
        # Target date before start
        with pytest.raises(ValidationError):
            g = FinancialGoal(
                user=user,
                name="Bad Date",
                target_amount=Decimal("1000.00"),
                currency=currency,
                start_date=date.today(),
                target_date=date.today() - timedelta(days=1),
            )
            g.clean()

    def test_goal_progress(self, goal):
        """Test progress calculation."""
        goal.current_amount = Decimal("10000.00")
        goal.save()

        assert goal.progress_percentage == Decimal("50.00")

    def test_goal_achievement(self, goal):
        """Test goal achievement logic."""
        goal.current_amount = Decimal("20000.00")
        goal.save()

        assert goal.is_achieved is True
        assert goal.achieved_date is not None
        assert goal.months_remaining == 0

    def test_months_remaining_calculation(self, goal):
        """Test calculation of remaining months."""
        # Target 20000, Current 0, Monthly 500 -> 40 months
        goal.save()  # Triggers calculation
        assert goal.months_remaining == 40

        # Increase contribution
        goal.monthly_contribution = Decimal("1000.00")
        goal.save()
        assert goal.months_remaining == 20
