import pytest
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Account
from core.models import Currency


@pytest.mark.django_db
class TestAccountAPI:
    """Test suite for Account API endpoints."""

    @pytest.fixture
    def client(self):
        return APIClient()

    @pytest.fixture
    def user(self, django_user_model):
        return django_user_model.objects.create_user(
            email="test@example.com", password="password"
        )

    @pytest.fixture
    def currency(self):
        return Currency.objects.create(
            code="USD", name="Dollar", symbol="$", exchange_rate=1.0, is_active=True
        )

    def test_create_account(self, client, user, currency):
        """Test creating an account via API."""
        client.force_authenticate(user=user)

        url = "/api/v1/accounts/"
        data = {
            "name": "My Savings",
            "account_type": "Savings",  # Case sensitive matching choices? TextChoices are usually title case 'Savings'
            "currency_id": currency.id,
            "initial_balance": "500.00",
        }
        response = client.post(url, data)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "My Savings"
        assert response.data["currency"]["code"] == "USD"
        # Serializer logic sets current_balance = initial_balance
        assert response.data["current_balance"] == "500.00"

    def test_list_accounts(self, client, user, currency):
        """Test listing user accounts."""
        Account.objects.create(user=user, currency=currency, name="Test Acc")
        client.force_authenticate(user=user)

        url = "/api/v1/accounts/"
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

    def test_update_account(self, client, user, currency):
        """Test partial update of account."""
        account = Account.objects.create(user=user, currency=currency, name="Old Name")
        client.force_authenticate(user=user)

        url = f"/api/v1/accounts/{account.id}/"
        data = {"name": "New Name"}
        response = client.patch(url, data)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "New Name"

        account.refresh_from_db()
        assert account.name == "New Name"

    def test_delete_account(self, client, user, currency):
        """Test deleting an account."""
        account = Account.objects.create(user=user, currency=currency, name="To Delete")
        client.force_authenticate(user=user)

        url = f"/api/v1/accounts/{account.id}/"
        response = client.delete(url)

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert Account.objects.filter(id=account.id).exists() is False
