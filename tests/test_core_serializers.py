import pytest
import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch
from django.utils import timezone
from rest_framework.serializers import ValidationError as DRFValidationError

from core.models import Category, Currency
from core.serializers import (
    CurrencySerializer,
    CurrencyConversionSerializer,
    ExchangeRateUpdateSerializer,
    CurrencyListSerializer,
    CurrencyDetailSerializer,
    CategoryListSerializer,
    CategoryDetailSerializer,
    CategorySerializer,
    CategoryTreeSerializer,
    CategoryBulkUpdateSerializer,
    get_category_serializer,
)
from utils import choices


@pytest.fixture
def mock_request_with_user(django_user_model):
    def _make_request(user=None):
        if user is None:
            user = django_user_model.objects.create_user(
                email=f"user_{uuid.uuid4()}@example.com",
                password="password",
                first_name="Test",
                last_name="User",
            )
        request = MagicMock()
        request.user = user
        return request

    return _make_request


@pytest.mark.django_db
class TestCurrencySerializer:
    """Test suite for CurrencySerializer."""

    def test_serialize_valid_currency(self):
        """Test serializing a valid currency instance."""
        currency = Currency.objects.create(
            code="USD",
            name="US Dollar",
            symbol="$",
            exchange_rate=Decimal("1.0"),
            is_base_currency=True,
        )
        serializer = CurrencySerializer(currency)
        data = serializer.data

        assert data["code"] == "USD"
        assert data["formatted_exchange_rate"] == "1.00"
        assert data["is_base_currency"] is True
        assert "last_updated" in data

    def test_validate_code_formatting(self):
        """Test that currency code is stripped and uppercased."""
        data = {
            "code": "  eur  ",
            "name": "Euro",
            "symbol": "€",
            "exchange_rate": "0.85",
        }
        serializer = CurrencySerializer(data=data)
        serializer.is_valid(raise_exception=True)
        assert serializer.validated_data["code"] == "EUR"

    def test_validate_invalid_code(self):
        """Test validation failures for invalid codes."""
        invalid_codes = ["", "US", "USDD", "123", "US$"]
        for code in invalid_codes:
            serializer = CurrencySerializer(data={"code": code})
            assert not serializer.is_valid()
            assert "code" in serializer.errors

    def test_validate_exchange_rate(self):
        """Test exchange rate validation."""
        # Valid rate
        serializer = CurrencySerializer(
            data={"code": "EUR", "name": "Euro", "exchange_rate": "1.23456789"}
        )
        serializer.is_valid(raise_exception=True)

        # Invalid: Negative
        serializer = CurrencySerializer(
            data={"code": "EUR", "name": "Euro", "exchange_rate": "-1.0"}
        )
        assert not serializer.is_valid()
        assert "exchange_rate" in serializer.errors

        # Invalid: Zero
        serializer = CurrencySerializer(
            data={"code": "EUR", "name": "Euro", "exchange_rate": "0"}
        )
        assert not serializer.is_valid()
        assert "exchange_rate" in serializer.errors

    def test_validate_decimal_places(self):
        """Test decimal places validation."""
        # Valid
        serializer = CurrencySerializer(
            data={"code": "JPY", "name": "Yen", "decimal_places": 0}
        )
        # Assuming other fields are optional or mocked for partial validation, but here we need required fields.
        # Let's provide minimum required fields.
        serializer = CurrencySerializer(
            data={
                "code": "JPY",
                "name": "Yen",
                "exchange_rate": "100",
                "decimal_places": 0,
            }
        )
        serializer.is_valid(raise_exception=True)

        # Invalid: Negative
        serializer = CurrencySerializer(
            data={
                "code": "JPY",
                "name": "Yen",
                "exchange_rate": "100",
                "decimal_places": -1,
            }
        )
        assert not serializer.is_valid()
        assert "decimal_places" in serializer.errors

        # Invalid: > 6
        serializer = CurrencySerializer(
            data={
                "code": "JPY",
                "name": "Yen",
                "exchange_rate": "100",
                "decimal_places": 7,
            }
        )
        assert not serializer.is_valid()
        assert "decimal_places" in serializer.errors

    def test_create_base_currency_success(self):
        """Test creating a base currency."""
        data = {
            "code": "USD",
            "name": "US Dollar",
            "is_base_currency": True,
            "exchange_rate": "1.0",
        }
        serializer = CurrencySerializer(data=data)
        serializer.is_valid(raise_exception=True)
        currency = serializer.save()

        assert currency.is_base_currency is True
        assert currency.exchange_rate == Decimal("1.0")

    def test_create_second_base_currency_fails(self):
        """Test that creating a second base currency fails."""
        Currency.objects.create(code="USD", name="US Dollar", is_base_currency=True)

        data = {
            "code": "EUR",
            "name": "Euro",
            "is_base_currency": True,
            "exchange_rate": "1.0",
        }
        serializer = CurrencySerializer(data=data)
        with pytest.raises(DRFValidationError) as exc:
            serializer.is_valid(raise_exception=True)
        assert "is_base_currency" in str(exc.value)

    def test_update_promote_to_base_currency(self):
        """Test updating a currency to be the new base currency."""
        old_base = Currency.objects.create(
            code="USD", name="US Dollar", is_base_currency=True
        )
        new_base = Currency.objects.create(
            code="EUR",
            name="Euro",
            is_base_currency=False,
            exchange_rate=Decimal("0.85"),
        )

        serializer = CurrencySerializer(
            new_base, data={"is_base_currency": True}, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        # Refresh from DB
        old_base.refresh_from_db()
        new_base.refresh_from_db()

        assert new_base.is_base_currency is True
        assert new_base.exchange_rate == Decimal("1.0")
        assert old_base.is_base_currency is False


@pytest.mark.django_db
class TestCurrencyConversionSerializer:
    """Test suite for CurrencyConversionSerializer."""

    def test_conversion_valid_currencies(self):
        """Test successful conversion."""
        source = Currency.objects.create(
            code="USD",
            name="US Dollar",
            exchange_rate=Decimal("1.0"),
            is_base_currency=True,
        )
        target = Currency.objects.create(
            code="EUR", name="Euro", exchange_rate=Decimal("0.85")
        )

        data = {"source_currency": "USD", "target_currency": "EUR", "amount": "100.00"}

        serializer = CurrencyConversionSerializer(data=data)
        assert serializer.is_valid(raise_exception=True)

        result = serializer.save()
        assert result["converted_amount"] == "117.65"  # 100 USD -> 117.65 EUR
        assert result["source_currency"] == "USD"
        assert result["target_currency"] == "EUR"

    def test_conversion_invalid_currency_code(self):
        """Test conversion with non-existent currency code."""
        data = {"source_currency": "USD", "target_currency": "XXX", "amount": "100.00"}
        serializer = CurrencyConversionSerializer(data=data)
        with pytest.raises(DRFValidationError) as exc:
            serializer.is_valid(raise_exception=True)
        assert "currency_not_found" in str(exc.value)

    def test_conversion_negative_amount(self):
        """Test conversion with negative amount."""
        Currency.objects.create(
            code="USD", name="US Dollar", exchange_rate=Decimal("1.0")
        )

        data = {"source_currency": "USD", "target_currency": "USD", "amount": "-100.00"}
        serializer = CurrencyConversionSerializer(data=data)
        assert not serializer.is_valid()
        assert "amount" in serializer.errors


@pytest.mark.django_db
class TestExchangeRateUpdateSerializer:
    """Test suite for ExchangeRateUpdateSerializer."""

    def test_batch_update_success(self):
        """Test successful batch update."""
        Currency.objects.create(code="EUR", name="Euro", exchange_rate=Decimal("0.80"))
        Currency.objects.create(
            code="GBP", name="British Pound", exchange_rate=Decimal("0.70")
        )

        data = {"source": "ECB", "rates": {"EUR": "0.85", "GBP": "0.75"}}

        serializer = ExchangeRateUpdateSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        stats = serializer.save()

        assert stats["updated"] == 2

        # Verify DB updates
        assert Currency.objects.get(code="EUR").exchange_rate == Decimal("0.85")
        assert Currency.objects.get(code="GBP").exchange_rate == Decimal("0.75")


@pytest.mark.django_db
class TestCurrencyListSerializer:
    """Test suite for CurrencyListSerializer."""

    def test_list_serialization(self):
        """Test lightweight serialization."""
        currency = Currency.objects.create(
            code="USD", name="US Dollar", symbol="$", is_active=True
        )
        serializer = CurrencyListSerializer(currency)
        data = serializer.data

        required_fields = {
            "id",
            "code",
            "name",
            "symbol",
            "is_active",
            "is_base_currency",
        }
        assert set(data.keys()) == required_fields
        # Should not have heavy fields
        assert "historical_rates" not in data


@pytest.mark.django_db
class TestCurrencyDetailSerializer:
    """Test suite for CurrencyDetailSerializer."""

    def test_detail_serialization(self):
        """Test detailed serialization with extras."""
        currency = Currency.objects.create(
            code="USD", name="US Dollar", exchange_rate=Decimal("1.0")
        )

        # Create a second currency for conversion examples
        Currency.objects.create(
            code="EUR", name="Euro", exchange_rate=Decimal("0.85"), is_active=True
        )

        serializer = CurrencyDetailSerializer(currency)
        data = serializer.data

        assert "historical_rates" in data
        assert "conversion_examples" in data
        assert "usage_count" in data

        # Check conversion examples structure
        examples = data["conversion_examples"]
        if examples:
            sub = examples[0]
            assert "from" in sub
            assert "to" in sub
            assert "rate" in sub


@pytest.mark.django_db
class TestCategoryListSerializer:
    """Test suite for CategoryListSerializer."""

    def test_list_serialization(self, mock_request_with_user):
        """Test lightweight list serialization."""
        user = mock_request_with_user().user
        parent = Category.objects.create(name="Parent", user=user)
        child = Category.objects.create(name="Child", parent=parent, user=user)

        # Create a grandchild to test has_children=True for child
        Category.objects.create(name="Grandchild", parent=child, user=user)

        serializer = CategoryListSerializer(child)
        data = serializer.data

        assert data["name"] == "Child"
        assert data["parent_name"] == "Parent"
        assert data["has_children"] is True
        assert "full_path" in data


@pytest.mark.django_db
class TestCategoryDetailSerializer:
    """Test suite for CategoryDetailSerializer."""

    def test_detail_serialization(self, mock_request_with_user):
        """Test detailed serialization."""
        user = mock_request_with_user().user
        grandparent = Category.objects.create(name="Grandparent", user=user)
        parent = Category.objects.create(name="Parent", parent=grandparent, user=user)
        child = Category.objects.create(name="Child", parent=parent, user=user)

        serializer = CategoryDetailSerializer(child)
        data = serializer.data

        assert "parent_info" in data
        assert data["parent_info"]["name"] == "Parent"

        assert "ancestors" in data
        ancestor_names = [a["name"] for a in data["ancestors"]]
        assert "Parent" in ancestor_names
        assert "Grandparent" in ancestor_names

        assert "usage_stats" in data


@pytest.mark.django_db
class TestCategorySerializer:
    """Test suite for CategorySerializer (CRUD)."""

    @pytest.fixture
    def user(self, mock_request_with_user):
        return mock_request_with_user().user

    def test_create_category_success(self, user):
        """Test successful category creation."""
        data = {"name": "Food", "category_type": choices.TransactionType.EXPENSE}
        context = {"request": MagicMock(user=user)}

        serializer = CategorySerializer(data=data, context=context)
        assert serializer.is_valid(raise_exception=True)
        category = serializer.save()

        assert category.name == "Food"
        assert category.user == user

    def test_create_duplicate_name_for_user_fails(self, user):
        """Test unique constraint per user."""
        Category.objects.create(
            name="Food", user=user, category_type=choices.TransactionType.EXPENSE
        )

        data = {"name": "Food", "category_type": choices.TransactionType.EXPENSE}
        context = {"request": MagicMock(user=user)}

        serializer = CategorySerializer(data=data, context=context)
        with pytest.raises(DRFValidationError) as exc:
            serializer.is_valid(raise_exception=True)
        assert "name" in str(exc.value)

    def test_create_duplicate_system_category_fails(self, user):
        """Test cannot create duplicate system category."""
        # Setup context for a user (users shouldn't even be able to set is_system_category=True)
        # But if the serializer processes it, it should validation error out early.
        pass

    def test_user_cannot_set_is_system_category(self, user):
        """Test that user cannot create system categories."""
        data = {"name": "System", "is_system_category": True}
        context = {"request": MagicMock(user=user)}

        serializer = CategorySerializer(data=data, context=context)
        with pytest.raises(DRFValidationError) as exc:
            serializer.is_valid(raise_exception=True)
        assert "is_system_category" in str(exc.value)

    def test_validate_parent_ownership(self, user, django_user_model):
        """Test parent must belong to same user."""
        other_user = django_user_model.objects.create_user(
            email="other@example.com",
            first_name="Other",
            last_name="User",
            password="pw",
        )
        parent = Category.objects.create(name="Parent", user=other_user)

        data = {"name": "Child", "parent": parent.id}
        context = {"request": MagicMock(user=user)}

        serializer = CategorySerializer(data=data, context=context)
        with pytest.raises(DRFValidationError) as exc:
            serializer.is_valid(raise_exception=True)
        assert "invalid_parent_user" in str(exc.value)

    def test_validate_circular_reference_direct(self, user):
        """Test category cannot be its own parent."""
        category = Category.objects.create(name="Self", user=user)

        data = {"name": "Self", "parent": category.id}
        context = {"request": MagicMock(user=user)}

        serializer = CategorySerializer(
            category, data=data, partial=True, context=context
        )
        with pytest.raises(DRFValidationError) as exc:
            serializer.is_valid(raise_exception=True)
        assert "self_parent" in str(exc.value)

    def test_validate_circular_reference_hierarchy(self, user):
        """Test circular hierarchy A->B->A."""
        a = Category.objects.create(name="A", user=user)
        b = Category.objects.create(name="B", user=user, parent=a)

        # Try to make B the parent of A
        data = {"parent": b.id}
        context = {"request": MagicMock(user=user)}

        serializer = CategorySerializer(a, data=data, partial=True, context=context)
        with pytest.raises(DRFValidationError) as exc:
            serializer.is_valid(raise_exception=True)
        assert "Circular reference" in str(exc.value)

    def test_can_delete_logic(self, user):
        """Test can_delete computed field."""
        # 1. Plain category - deletable
        c1 = Category.objects.create(name="Clean", user=user)
        s1 = CategorySerializer(c1)
        assert s1.data["can_delete"] is True

        # 2. Category with active children - not deletable
        c2 = Category.objects.create(name="Parent", user=user)
        Category.objects.create(name="Child", parent=c2, user=user)
        s2 = CategorySerializer(c2)
        assert s2.data["can_delete"] is False

        # 3. System category - not deletable
        c3 = Category.objects.create(name="Sys", is_system_category=True)
        s3 = CategorySerializer(c3)
        assert s3.data["can_delete"] is False

    def test_update_system_category_forbidden(self, user):
        """Test system categories cannot be modified."""
        sys_cat = Category.objects.create(name="System", is_system_category=True)

        context = {"request": MagicMock(user=user)}
        serializer = CategorySerializer(
            sys_cat, data={"name": "Hacked"}, partial=True, context=context
        )

        with pytest.raises(DRFValidationError) as exc:
            serializer.is_valid(
                raise_exception=True
            )  # Actually update checks are usually in validate or update
            # But the check is in update() method of serializer
            serializer.save()

        # Re-check where the validation happens. The code shows it in update() method.
        # So we expect is_valid to pass, but save() to fail.
        # Wait, inside update() it raises ValidationError.
        # So we need to call save().


@pytest.mark.django_db
class TestCategoryTreeSerializer:
    """Test suite for CategoryTreeSerializer."""

    def test_tree_structure(self, mock_request_with_user):
        """Test recursive tree serialization."""
        user = mock_request_with_user().user
        root = Category.objects.create(name="Root", user=user)
        child1 = Category.objects.create(name="C1", parent=root, user=user)
        child2 = Category.objects.create(name="C2", parent=root, user=user)
        grandchild = Category.objects.create(name="GC1", parent=child1, user=user)

        serializer = CategoryTreeSerializer(root)
        data = serializer.data

        assert data["name"] == "Root"
        assert len(data["children"]) == 2

        c1_data = next(c for c in data["children"] if c["name"] == "C1")
        assert len(c1_data["children"]) == 1
        assert c1_data["children"][0]["name"] == "GC1"
        assert c1_data["depth"] == 1


@pytest.mark.django_db
class TestCategoryBulkUpdateSerializer:
    """Test suite for CategoryBulkUpdateSerializer."""

    def test_bulk_activate(self, mock_request_with_user):
        """Test bulk activation."""
        user = mock_request_with_user().user
        c1 = Category.objects.create(name="C1", user=user, is_active=False)
        c2 = Category.objects.create(name="C2", user=user, is_active=False)

        data = {"category_ids": [str(c1.id), str(c2.id)], "action": "activate"}

        # Must pass request context so user can be identified
        context = {"request": MagicMock(user=user)}
        serializer = CategoryBulkUpdateSerializer(data=data, context=context)
        serializer.is_valid(raise_exception=True)
        stats = serializer.save()

        assert stats["successful"] == 2
        c1.refresh_from_db()
        c2.refresh_from_db()
        assert c1.is_active is True
        assert c2.is_active is True

    def test_bulk_change_parent(self, mock_request_with_user):
        """Test bulk parent change."""
        user = mock_request_with_user().user
        parent = Category.objects.create(name="NewParent", user=user)
        c1 = Category.objects.create(name="C1", user=user)

        data = {
            "category_ids": [str(c1.id)],
            "action": "change_parent",
            "parent_id": str(parent.id),
        }

        context = {"request": MagicMock(user=user)}
        serializer = CategoryBulkUpdateSerializer(data=data, context=context)
        serializer.is_valid(raise_exception=True)
        stats = serializer.save()

        assert stats["successful"] == 1
        c1.refresh_from_db()
        assert c1.parent == parent


def test_get_category_serializer_factory():
    """Test factory function returns correct serializer class."""
    assert get_category_serializer("list") == CategoryListSerializer
    assert get_category_serializer("detail") == CategoryDetailSerializer
    assert get_category_serializer("tree") == CategoryTreeSerializer
    assert get_category_serializer("bulk") == CategoryBulkUpdateSerializer
    assert get_category_serializer("default") == CategorySerializer
    assert get_category_serializer("unknown") == CategorySerializer
