import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.db import models
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Category, Currency
from utils import choices


@pytest.mark.django_db
class TestCurrencyAPI:
    """Currency API endpoint tests."""

    @pytest.fixture
    def client(self):
        return APIClient()

    @pytest.fixture
    def admin_user(self, django_user_model):
        return django_user_model.objects.create_superuser(
            email=f"admin_{uuid.uuid4()}@example.com",
            password="pass",
            first_name="Admin",
            last_name="User",
            is_active=True,
            is_verified=True,
        )

    @pytest.fixture
    def normal_user(self, django_user_model):
        return django_user_model.objects.create_user(
            email=f"user_{uuid.uuid4()}@example.com",
            password="pass",
            first_name="Normal",
            last_name="User",
            is_active=True,
            is_verified=True,
        )

    @pytest.fixture
    def inactive_user(self, django_user_model):
        return django_user_model.objects.create_user(
            email=f"inactive_{uuid.uuid4()}@example.com",
            password="pass",
            first_name="Inactive",
            last_name="User",
            is_active=False,
            is_verified=True,
        )

    @pytest.fixture
    def unverified_user(self, django_user_model):
        return django_user_model.objects.create_user(
            email=f"unverified_{uuid.uuid4()}@example.com",
            password="pass",
            first_name="Unverified",
            last_name="User",
            is_active=True,
            is_verified=False,
        )

    @pytest.fixture
    def currency(self):
        return Currency.objects.create(
            code="USD",
            name="US Dollar",
            symbol="$",
            exchange_rate=Decimal("1.0"),
            is_active=True,
            is_base_currency=True,
        )

    @pytest.fixture
    def inactive_currency(self):
        return Currency.objects.create(
            code="EUR",
            name="Euro Old",
            symbol="€",
            exchange_rate=Decimal("0.85"),
            is_active=False,
        )

    # ---------------------------
    # Authentication Tests
    # ---------------------------

    def test_list_currencies_unauthenticated(self, client):
        """Unauthenticated users should be denied."""
        response = client.get("/core/currencies/")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_list_currencies_inactive_user(self, client, inactive_user):
        """Inactive users should be denied."""
        client.force_authenticate(user=inactive_user)
        response = client.get("/core/currencies/")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_list_currencies_unverified_user(self, client, unverified_user):
        """Unverified users should be denied details access."""
        # Typically unverified users might have limited access, depending on policy.
        # Based on IsActiveAndVerified permission class logic:
        client.force_authenticate(user=unverified_user)
        response = client.get("/core/currencies/")
        # If permission is strict:
        assert response.status_code == status.HTTP_403_FORBIDDEN

    # ---------------------------
    # List/Retrieve Tests
    # ---------------------------

    def test_list_currencies_authenticated(self, client, normal_user, currency):
        """Authenticated users can list active currencies."""
        client.force_authenticate(user=normal_user)
        response = client.get("/core/currencies/")
        assert response.status_code == status.HTTP_200_OK

        # DRF pagination wrapping check
        results = response.data.get("data", response.data)
        assert len(results) >= 1
        assert results[0]["code"] == "USD"

    def test_list_currencies_hides_inactive_for_normal_user(
        self, client, normal_user, currency, inactive_currency
    ):
        """Regular users should not see inactive currencies."""
        client.force_authenticate(user=normal_user)
        response = client.get("/core/currencies/")
        results = response.data.get("data", response.data)

        codes = [c["code"] for c in results]
        assert "USD" in codes
        assert "EUR" not in codes

    def test_list_currencies_admin_sees_all(
        self, client, admin_user, currency, inactive_currency
    ):
        """Admins can see inactive currencies if requested or by default depending on implementation."""
        client.force_authenticate(user=admin_user)
        # By default viewset might show all if user is staff
        response = client.get("/core/currencies/")
        results = response.data.get("data", response.data)
        codes = [c["code"] for c in results]
        # In the view implementation:
        # if not (user.is_staff or user.is_superuser): queryset = queryset.filter(is_active=True)
        # This implies staff sees ALL by default.
        assert "USD" in codes
        assert "EUR" in codes

    def test_list_currencies_search(self, client, normal_user, currency):
        """Search functionality."""
        client.force_authenticate(user=normal_user)
        response = client.get("/core/currencies/?search=dollar")
        results = response.data.get("data", response.data)
        assert len(results) == 1
        assert results[0]["code"] == "USD"

    def test_retrieve_currency(self, client, normal_user, currency):
        """Retrieve details of a currency."""
        client.force_authenticate(user=normal_user)
        response = client.get(f"/core/currencies/{currency.id}/")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["code"] == "USD"

    def test_retrieve_inactive_currency_forbidden(
        self, client, normal_user, inactive_currency
    ):
        """Regular user cannot retrieve inactive currency (Not Found)."""
        client.force_authenticate(user=normal_user)
        response = client.get(f"/core/currencies/{inactive_currency.id}/")
        # Usually filtered out of queryset -> 404
        assert response.status_code == status.HTTP_404_NOT_FOUND

    # ---------------------------
    # Create/Update Tests (Admin Only)
    # ---------------------------

    def test_create_currency_admin(self, client, admin_user):
        """Admin can create currency."""
        client.force_authenticate(user=admin_user)
        data = {
            "code": "GBP",
            "name": "British Pound",
            "symbol": "£",
            "exchange_rate": "1.3",
        }
        response = client.post("/core/currencies/", data)
        assert response.status_code == status.HTTP_201_CREATED
        assert Currency.objects.get(code="GBP").name == "British Pound"

    def test_create_currency_normal_user_denied(self, client, normal_user):
        """Normal user cannot create currency."""
        client.force_authenticate(user=normal_user)
        data = {"code": "GBP", "name": "Pound"}
        response = client.post("/core/currencies/", data)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_update_currency_admin(self, client, admin_user, currency):
        """Admin can update currency."""
        client.force_authenticate(user=admin_user)
        data = {"name": "USD Updated"}
        response = client.patch(f"/core/currencies/{currency.id}/", data)
        assert response.status_code == status.HTTP_200_OK
        currency.refresh_from_db()
        assert currency.name == "USD Updated"

    def test_update_base_currency_action_admin(self, client, admin_user, currency):
        """Admin can update base currency via custom action."""
        # Create another currency to promote
        new_base = Currency.objects.create(
            code="EUR", name="Euro", exchange_rate=Decimal("0.85"), is_active=True
        )

        client.force_authenticate(user=admin_user)
        response = client.post(f"/core/currencies/{new_base.id}/set-base/")
        assert response.status_code == status.HTTP_200_OK

        new_base.refresh_from_db()
        currency.refresh_from_db()

        assert new_base.is_base_currency is True
        assert new_base.exchange_rate == Decimal("1.0")
        assert currency.is_base_currency is False

    def test_update_exchange_rates_action(self, client, admin_user, currency):
        """Admin can batch update rates."""
        other = Currency.objects.create(
            code="EUR", name="Euro", exchange_rate=Decimal("0.85")
        )

        client.force_authenticate(user=admin_user)
        data = {"source": "ECB", "rates": {"EUR": "0.90", "USD": "1.0"}}
        response = client.post("/core/currencies/update-rates/", data, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["updated"] == 2

        other.refresh_from_db()
        assert other.exchange_rate == Decimal("0.90")

    # ---------------------------
    # Conversion Tests
    # ---------------------------
    def test_convert_currency_endpoint(self, client, normal_user):
        """Test public conversion endpoint."""
        Currency.objects.create(
            code="UDD",
            name="Dollar",
            exchange_rate=Decimal("1.0"),
            is_base_currency=True,
        )
        Currency.objects.create(code="ERR", name="Eur", exchange_rate=Decimal("0.85"))

        client.force_authenticate(user=normal_user)
        data = {"source_currency": "UDD", "target_currency": "ERR", "amount": "100.00"}
        response = client.post("/core/currencies/convert/", data)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["converted_amount"] == "117.65"


@pytest.mark.django_db
class TestCategoryAPI:
    """Category API endpoint tests."""

    @pytest.fixture
    def client(self):
        return APIClient()

    @pytest.fixture
    def normal_user(self, django_user_model):
        return django_user_model.objects.create_user(
            email=f"user_{uuid.uuid4()}@example.com",
            password="pass",
            first_name="Normal",
            last_name="User",
            is_active=True,
            is_verified=True,
        )

    @pytest.fixture
    def admin_user(self, django_user_model):
        return django_user_model.objects.create_superuser(
            email=f"admin_{uuid.uuid4()}@example.com",
            password="pass",
            first_name="Admin",
            last_name="User",
            is_active=True,
            is_verified=True,
        )

    @pytest.fixture
    def user_category(self, normal_user):
        return Category.objects.create(
            name="Food", user=normal_user, category_type=choices.TransactionType.EXPENSE
        )

    @pytest.fixture
    def system_category(self):
        return Category.objects.create(
            name="System Cat",
            is_system_category=True,
            category_type=choices.TransactionType.EXPENSE,
        )

    # ---------------------------
    # CRUD Tests
    # ---------------------------

    def test_list_categories(self, client, normal_user, user_category, system_category):
        """User sees their own + system categories."""
        client.force_authenticate(user=normal_user)
        response = client.get("/core/categories/")

        # Handle potential list or dict response
        if isinstance(response.data, list):
            results = response.data
        else:
            results = response.data.get("results", response.data.get("data", []))

        names = [c["name"] for c in results]
        assert "Food" in names
        assert "System Cat" in names

    def test_create_category_user(self, client, normal_user):
        """User creates personal category."""
        client.force_authenticate(user=normal_user)
        data = {"name": "Personal", "category_type": choices.TransactionType.EXPENSE}
        response = client.post("/core/categories/", data)
        assert response.status_code == status.HTTP_201_CREATED
        cat = Category.objects.get(name="Personal")
        assert cat.user == normal_user
        assert not cat.is_system_category

    def test_create_category_admin(self, client, admin_user):
        """Admin creating category makes it system category."""
        client.force_authenticate(user=admin_user)
        data = {"name": "Global", "category_type": choices.TransactionType.EXPENSE}
        response = client.post("/core/categories/", data)
        assert response.status_code == status.HTTP_201_CREATED
        cat = Category.objects.get(name="Global")
        assert cat.user is None
        assert cat.is_system_category

    def test_update_category_user_owned(self, client, normal_user, user_category):
        """User updates own category."""
        client.force_authenticate(user=normal_user)
        data = {"name": "Food Updated"}
        response = client.patch(f"/core/categories/{user_category.id}/", data)
        assert response.status_code == status.HTTP_200_OK
        user_category.refresh_from_db()
        assert user_category.name == "Food Updated"

    def test_update_category_system_denied_for_user(
        self, client, normal_user, system_category
    ):
        """User cannot update system category."""
        client.force_authenticate(user=normal_user)
        data = {"name": "Hacked"}
        response = client.patch(f"/core/categories/{system_category.id}/", data)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_delete_category(self, client, normal_user, user_category):
        """User can delete own category (soft delete)."""
        client.force_authenticate(user=normal_user)
        response = client.delete(f"/core/categories/{user_category.id}/")
        assert response.status_code == status.HTTP_204_NO_CONTENT
        user_category.refresh_from_db()
        assert user_category.is_active is False

    # ---------------------------
    # Bulk Ops (Admin Only)
    # ---------------------------
    def test_bulk_create_categories_admin(self, client, admin_user):
        """Admin can bulk create categories."""
        client.force_authenticate(user=admin_user)
        data = [
            {"name": "Bulk1", "category_type": choices.TransactionType.EXPENSE},
            {"name": "Bulk2", "category_type": choices.TransactionType.EXPENSE},
        ]
        response = client.post("/core/categories/bulk-create/", data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert Category.objects.filter(name__startswith="Bulk").count() == 2

    # ---------------------------
    # Tree View Tests
    # ---------------------------
    def test_category_tree(self, client, normal_user):
        """Test tree structure retrieval."""
        parent = Category.objects.create(
            name="Parent",
            user=normal_user,
            category_type=choices.TransactionType.EXPENSE,
        )
        Category.objects.create(
            name="Child",
            parent=parent,
            user=normal_user,
            category_type=choices.TransactionType.EXPENSE,
        )

        client.force_authenticate(user=normal_user)
        response = client.get("/core/categories/tree/")
        assert response.status_code == status.HTTP_200_OK

        # Should return root nodes
        data = response.data

        root = next((c for c in data if c["name"] == "Parent"), None)
        assert root is not None
        assert "children" in root
        assert len(root["children"]) == 1
        assert root["children"][0]["name"] == "Child"
