import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Currency


@pytest.mark.django_db
class TestCurrencyAPI:
    """Test suite for Currency API endpoints."""

    @pytest.fixture
    def client(self):
        return APIClient()

    @pytest.fixture
    def currency(self):
        return Currency.objects.create(
            code="USD", name="US Dollar", symbol="$", exchange_rate=1.00, is_active=True
        )

    def test_list_currencies(self, client, currency):
        """Test listing currencies (public access)."""
        url = "/api/v1/core/currencies/"
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1
        assert response.data["results"][0]["code"] == "USD"

    def test_create_currency_as_admin(self, client, django_user_model):
        """Test creating currency as admin."""
        admin_user = django_user_model.objects.create_superuser(
            "admin@example.com", "pass"
        )
        client.force_authenticate(user=admin_user)

        url = "/api/v1/core/currencies/"
        data = {"code": "EUR", "name": "Euro", "symbol": "€", "exchange_rate": 0.85}
        response = client.post(url, data)

        assert response.status_code == status.HTTP_201_CREATED
        assert Currency.objects.count() == 1
        assert Currency.objects.get().code == "EUR"

    def test_create_currency_permission_denied(self, client, django_user_model):
        """Test creating currency as normal user is forbidden."""
        user = django_user_model.objects.create_user(
            email="user@example.com", password="pass"
        )
        client.force_authenticate(user=user)

        url = "/api/v1/core/currencies/"
        data = {"code": "GBP", "name": "Pound", "symbol": "£", "exchange_rate": 0.75}
        response = client.post(url, data)

        assert response.status_code == status.HTTP_403_FORBIDDEN
