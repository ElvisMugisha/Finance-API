from datetime import date
from decimal import Decimal

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Account, Transaction
from core.models import Category, Currency
from utils import choices


@pytest.mark.django_db
class TestAccountViewSet:
    """Test suite for Account API endpoints."""

    base_url = "/accounts/"

    @pytest.fixture
    def client(self):
        return APIClient()

    @pytest.fixture
    def user(self, django_user_model):
        user = django_user_model.objects.create_user(
            email="test@example.com",
            password="password",
            first_name="Test",
            last_name="User",
            is_verified=True,
        )
        return user

    @pytest.fixture
    def other_user(self, django_user_model):
        user = django_user_model.objects.create_user(
            email="other@example.com",
            password="password",
            first_name="Other",
            last_name="User",
            is_verified=True,
        )
        return user

    @pytest.fixture
    def admin_user(self, django_user_model):
        user = django_user_model.objects.create_superuser(
            email="admin@example.com",
            password="password",
            first_name="Admin",
            last_name="User",
        )
        return user

    @pytest.fixture
    def currency(self):
        return Currency.objects.create(
            code="USD", name="Dollar", symbol="$", exchange_rate=1.0, is_active=True
        )

    @pytest.fixture
    def account(self, user, currency):
        return Account.objects.create(user=user, currency=currency, name="Test Account")

    @pytest.fixture
    def other_user_account(self, other_user, currency):
        return Account.objects.create(
            user=other_user, currency=currency, name="Other User Account"
        )

    # === Authentication and Permission Tests ===

    def test_unauthenticated_access_denied(self, client):
        """Test that unauthenticated users cannot access any account endpoints."""
        response = client.get(self.base_url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

        response = client.post(self.base_url, {})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_list_accounts_owner_only(self, client, user, account, other_user_account):
        """Test that a user can only list their own accounts."""
        client.force_authenticate(user=user)
        response = client.get(self.base_url)

        assert response.status_code == status.HTTP_200_OK
        # The response is paginated, so we check the 'data'
        assert len(response.data["data"]) == 1
        assert response.data["data"][0]["id"] == str(account.id)

    def test_retrieve_account_owner_only(self, client, user, account):
        """Test that a user can retrieve their own account."""
        client.force_authenticate(user=user)
        url = f"{self.base_url}{account.id}/"
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] == str(account.id)

    def test_cannot_access_other_user_account(self, client, user, other_user_account):
        """Test that a user cannot retrieve, update, or delete another user's account."""
        client.force_authenticate(user=user)
        url = f"{self.base_url}{other_user_account.id}/"

        # Retrieve
        response = client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

        # Update
        response = client.patch(url, {"name": "New Name"})
        assert response.status_code == status.HTTP_404_NOT_FOUND

        # Delete
        response = client.delete(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_admin_can_access_any_account(self, client, admin_user, account):
        """Test that an admin user can access any user's account."""
        client.force_authenticate(user=admin_user)
        url = f"{self.base_url}{account.id}/"

        response = client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] == str(account.id)

    # === CRUD Tests ===

    def test_create_account(self, client, user, currency):
        """Test creating an account via API."""
        client.force_authenticate(user=user)

        data = {
            "name": "My Savings",
            "account_type": choices.AccountType.SAVINGS.value,
            "currency_id": currency.id,
            "initial_balance": "500.00",
        }
        response = client.post(self.base_url, data)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "My Savings"
        assert response.data["currency"]["code"] == "USD"
        assert response.data["current_balance"] == "500.00"

    def test_create_account_invalid_data(self, client, user):
        """Test creating an account with missing required data fails."""
        client.force_authenticate(user=user)
        data = {"name": "Incomplete Account"}  # Missing currency_id and type
        response = client.post(self.base_url, data)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "currency_id" in response.data
        assert "account_type" in response.data

    def test_update_account(self, client, user, account):
        """Test partial update of account."""
        client.force_authenticate(user=user)

        url = f"{self.base_url}{account.id}/"
        data = {"name": "New Name"}
        response = client.patch(url, data)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "New Name"
        account.refresh_from_db()
        assert account.name == "New Name"

    def test_delete_account(self, client, user, account):
        """Test soft-deleting an account."""
        client.force_authenticate(user=user)

        url = f"{self.base_url}{account.id}/"
        response = client.delete(url)

        assert response.status_code == status.HTTP_204_NO_CONTENT
        account.refresh_from_db()
        assert account.is_active is False


@pytest.mark.django_db
class TestTransactionViewSet:
    """Test suite for Transaction API endpoints."""

    base_url = "/accounts/transactions/"

    @pytest.fixture
    def client(self):
        return APIClient()

    @pytest.fixture
    def user(self, django_user_model):
        return django_user_model.objects.create_user(
            email="test@example.com",
            password="password",
            is_verified=True,
            first_name="User",
            last_name="Test",
        )

    @pytest.fixture
    def other_user(self, django_user_model):
        return django_user_model.objects.create_user(
            email="other@example.com",
            password="password",
            is_verified=True,
            first_name="User",
            last_name="Test",
        )

    @pytest.fixture
    def currency(self):
        return Currency.objects.create(code="USD", name="US Dollar", symbol="$")

    @pytest.fixture
    def account(self, user, currency):
        return Account.objects.create(
            user=user,
            name="Checking",
            currency=currency,
            initial_balance=Decimal("1000.00"),
        )

    @pytest.fixture
    def category(self, user):
        return Category.objects.create(
            user=user, name="Groceries", category_type=choices.TransactionType.EXPENSE
        )

    @pytest.fixture
    def transaction(self, user, account, category):
        return Transaction.objects.create(
            user=user,
            account=account,
            category=category,
            name="Weekly Shopping",
            transaction_type=choices.TransactionType.EXPENSE,
            amount=Decimal("75.50"),
            currency=account.currency.code,
        )

    @pytest.fixture
    def other_user_transaction(self, other_user, account, category):
        # Note: account and category can be shared for simplicity if not testing ownership here
        return Transaction.objects.create(
            user=other_user,
            account=account,
            category=category,
            name="Other User Shopping",
            transaction_type=choices.TransactionType.EXPENSE,
            amount=Decimal("50.00"),
            currency=account.currency.code,
        )

    # === Authentication and Permission Tests ===

    def test_unauthenticated_access_denied(self, client):
        """Test that unauthenticated users cannot access transaction endpoints."""
        response = client.get(self.base_url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_list_transactions_owner_only(
        self, client, user, transaction, other_user_transaction
    ):
        """Test that a user can only list their own transactions."""
        client.force_authenticate(user=user)
        response = client.get(self.base_url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["data"]) == 1
        assert response.data["data"][0]["id"] == str(transaction.id)

    def test_cannot_access_other_user_transaction(
        self, client, user, other_user_transaction
    ):
        """Test that a user cannot retrieve, update, or delete another user's transaction."""
        client.force_authenticate(user=user)
        url = f"{self.base_url}{other_user_transaction.pk}/"

        response = client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

        response = client.patch(url, {"name": "New Name"})
        assert response.status_code == status.HTTP_404_NOT_FOUND

        response = client.delete(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    # === CRUD Tests ===

    def test_create_transaction(self, client, user, account, category):
        """Test creating a valid transaction."""
        client.force_authenticate(user=user)
        data = {
            "account": account.id,
            "category": category.id,
            "name": "Dinner Out",
            "transaction_type": choices.TransactionType.EXPENSE.value,
            "amount": "45.00",
            "currency": account.currency.code,
            "transaction_date": date.today().isoformat(),
        }
        response = client.post(self.base_url, data)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Dinner Out"
        assert response.data["amount"] == "45.00"
        assert Transaction.objects.count() == 1

    def test_update_transaction(self, client, user, transaction):
        """Test partially updating a transaction."""
        client.force_authenticate(user=user)
        url = f"{self.base_url}{transaction.pk}/"
        data = {"name": "Updated Shopping", "amount": "80.00"}
        response = client.patch(url, data)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Updated Shopping"
        assert response.data["amount"] == "80.00"
        transaction.refresh_from_db()
        assert transaction.name == "Updated Shopping"

    def test_delete_transaction(self, client, user, transaction):
        """Test deleting a transaction."""
        client.force_authenticate(user=user)
        url = f"{self.base_url}{transaction.pk}/"
        response = client.delete(url)

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not Transaction.objects.filter(pk=transaction.pk).exists()
