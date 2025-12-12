import pytest
from rest_framework.serializers import ValidationError as DRFValidationError

from core.models import Category, Currency
from core.serializers import CategorySerializer, CurrencySerializer
from utils import choices


@pytest.mark.django_db
class TestCurrencySerializer:

    def test_serialize_currency(self):
        currency = Currency.objects.create(
            code="usd", name="US Dollar", symbol="$", exchange_rate=1
        )
        currency.full_clean()
        currency.save()
        serializer = CurrencySerializer(currency)
        data = serializer.data
        assert data["code"] == "USD"
        assert data["name"] == "US Dollar"
        assert data["symbol"] == "$"
        assert data["exchange_rate"] == "1.000000"
        assert data["is_active"] is True
        assert "updated_at" in data

    def test_validate_code_uppercases(self):
        data = {"code": "eur", "name": "Euro", "exchange_rate": 0.85}
        serializer = CurrencySerializer(data=data)
        serializer.is_valid(raise_exception=True)
        assert serializer.validated_data["code"] == "EUR"


@pytest.mark.django_db
class TestCategorySerializer:

    @pytest.fixture
    def user(self, django_user_model):
        return django_user_model.objects.create_user(
            email="catuser@example.com",
            first_name="Cat",
            last_name="User",
            password="password",
        )

    def test_create_category_success(self, user):
        data = {"name": "Food"}
        serializer = CategorySerializer(
            data=data, context={"request": type("Req", (), {"user": user})()}
        )
        assert serializer.is_valid(raise_exception=True)
        category = serializer.save()
        assert category.name == "Food"
        assert category.user == user
        assert category.is_system_category is False
        assert category.category_type == choices.TransactionType.EXPENSE

    def test_duplicate_category_name_fails(self, user):
        Category.objects.create(name="Food", user=user)
        data = {"name": "Food"}
        serializer = CategorySerializer(
            data=data, context={"request": type("Req", (), {"user": user})()}
        )
        with pytest.raises(DRFValidationError) as exc:
            serializer.is_valid(raise_exception=True)
        assert "name" in str(exc.value)
        assert "category_type" in str(exc.value)

    def test_parent_category_belongs_to_same_user(self, user, django_user_model):
        other_user = django_user_model.objects.create_user(
            email="other@example.com",
            first_name="Other",
            last_name="User",
            password="password",
        )
        parent = Category.objects.create(name="Parent", user=other_user)
        data = {"name": "Child", "parent": parent.id}
        serializer = CategorySerializer(
            data=data, context={"request": type("Req", (), {"user": user})()}
        )
        with pytest.raises(DRFValidationError) as exc:
            serializer.is_valid(raise_exception=True)
        assert "parent" in str(exc.value)

    def test_update_regular_category(self, user):
        category = Category.objects.create(name="Food", user=user)
        serializer = CategorySerializer(
            category,
            data={"name": "Groceries"},
            partial=True,
            context={"request": type("Req", (), {"user": user})()},
        )
        assert serializer.is_valid(raise_exception=True)
        updated = serializer.save()
        assert updated.name == "Groceries"

    def test_update_system_category_forbidden(self, user):
        category = Category.objects.create(name="SystemCat", is_system_category=True)
        serializer = CategorySerializer(
            category,
            data={"name": "Hacked"},
            partial=True,
            context={"request": type("Req", (), {"user": user})()},
        )
        with pytest.raises(DRFValidationError):
            serializer.is_valid(raise_exception=True)
            serializer.save()

    # -----------------------
    # New tests for parent_name
    # -----------------------

    def test_parent_name_read_only_field(self, user):
        parent = Category.objects.create(name="Parent", user=user)
        child = Category.objects.create(name="Child", parent=parent, user=user)
        serializer = CategorySerializer(child)
        data = serializer.data
        assert data["parent_name"] == "Parent"

    def test_parent_field_is_serialized_as_id(self, user):
        parent = Category.objects.create(name="Parent", user=user)
        child = Category.objects.create(name="Child", parent=parent, user=user)
        serializer = CategorySerializer(child)
        data = serializer.data
        assert data["parent"] == str(parent.id) or data["parent"] == parent.id

    def test_nested_hierarchy_serialization(self, user):
        grandparent = Category.objects.create(name="Grandparent", user=user)
        parent = Category.objects.create(name="Parent", parent=grandparent, user=user)
        child = Category.objects.create(name="Child", parent=parent, user=user)
        serializer = CategorySerializer(child)
        data = serializer.data
        # parent_name should point to direct parent
        assert data["parent_name"] == "Parent"
        # parent ID should point to parent object
        assert data["parent"] == parent.id
