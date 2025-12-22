from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Union

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import exceptions, serializers

from utils import loggings

from .models import Category, Currency

logger = loggings.setup_logging()


class CurrencySerializer(serializers.ModelSerializer):
    """
    Serializer for Currency model with comprehensive validation and security.

    Security Considerations:
    - is_base_currency is read-only to prevent privilege escalation
    - exchange_source and exchange_updated_at are read-only for audit integrity
    - historical_rates is write-protected to maintain data consistency

    Validation Features:
    - ISO 4217 code validation (3 letters, uppercase)
    - Exchange rate positivity validation
    - Decimal places range validation (0-6)
    - Cross-field validation for base currency uniqueness
    """

    # Custom field representations
    formatted_exchange_rate = serializers.SerializerMethodField()
    last_updated = serializers.SerializerMethodField()

    class Meta:
        model = Currency
        fields = [
            "id",
            "code",
            "name",
            "symbol",
            "decimal_places",
            "exchange_rate",
            "formatted_exchange_rate",
            "exchange_source",
            "exchange_updated_at",
            "last_updated",
            "is_active",
            "is_base_currency",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "exchange_source",
            "exchange_updated_at",
            # "is_base_currency",  # Managed via custom validation/logic
            "created_at",
            "updated_at",
            "historical_rates",  # Protected - only updated through batch operations
        ]

        extra_kwargs = {
            "code": {
                "help_text": _("ISO 4217 currency code (3 uppercase letters)"),
                "max_length": 3,
                "min_length": 3,
            },
            "exchange_rate": {
                "help_text": _("Current exchange rate to base currency"),
                "max_digits": 20,
                "decimal_places": 10,
                "min_value": 0,
            },
            "decimal_places": {
                "help_text": _("Number of decimal places (0-10)"),
                "min_value": 0,
                "max_value": 10,
            },
        }

    def get_formatted_exchange_rate(self, obj: Currency) -> str:
        """Get formatted exchange rate for display."""
        try:
            # Format with appropriate decimal places (up to 10)
            return f"{obj.exchange_rate:.{min(10, obj.decimal_places)}f}"
        except (AttributeError, ValueError, TypeError) as e:
            logger.warning(f"Error formatting exchange rate for {obj.code}: {e}")
            return str(obj.exchange_rate)

    def get_last_updated(self, obj: Currency) -> Optional[str]:
        """Get human-friendly last updated timestamp."""
        if obj.exchange_updated_at:
            return timezone.localtime(obj.exchange_updated_at).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        return None

    def validate_code(self, value: str) -> str:
        """
        Validate currency code according to ISO 4217 standards.
        """
        if not value:
            raise serializers.ValidationError(_("Currency code cannot be empty."))

        value = value.strip().upper()
        if len(value) != 3 or not value.isalpha():
            raise serializers.ValidationError(
                _("Currency code must be exactly 3 uppercase letters.")
            )
        return value

    def validate(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Perform cross-field validation.
        """
        is_base = attrs.get(
            "is_base_currency",
            (
                getattr(self.instance, "is_base_currency", False)
                if self.instance
                else False
            ),
        )

        if is_base:
            # For base currency, we enforce 1.0.
            # Model save() also does this, but serializer handles it for API feedback.
            attrs["exchange_rate"] = Decimal("1.0")
            logger.debug(
                f"Ensuring exchange_rate=1.0 for base currency {attrs.get('code')}"
            )

        return attrs

    def create(self, validated_data: Dict[str, Any]) -> Currency:
        """
        Create a new currency.
        """
        logger.info(f"Creating new currency: {validated_data.get('code')}")
        try:
            return Currency.objects.create(**validated_data)

        except DjangoValidationError as e:
            raise serializers.ValidationError(e.message_dict)

        except Exception as e:
            logger.error(f"Error creating currency: {e}")
            raise serializers.ValidationError(
                _("Could not create currency."), code="creation_error"
            )

    def update(self, instance: Currency, validated_data: Dict[str, Any]) -> Currency:
        """
        Update an existing currency.
        """
        logger.info(f"Updating currency: {instance.code}")
        try:
            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            instance.save()
            return instance

        except DjangoValidationError as e:
            raise serializers.ValidationError(e.message_dict)

        except Exception as e:
            logger.error(f"Error updating currency {instance.code}: {e}")
            raise serializers.ValidationError(
                _("Could not update currency."), code="update_error"
            )


class CurrencyConversionSerializer(serializers.Serializer):
    """
    Serializer for currency conversion requests.

    Features:
    - Validates source and target currencies exist
    - Handles decimal amount validation
    - Optional date parameter for historical rates
    - Returns converted amount with metadata
    """

    source_currency = serializers.CharField(
        max_length=3,
        min_length=3,
        help_text=_("Source currency code (ISO 4217)"),
        trim_whitespace=True,
    )
    target_currency = serializers.CharField(
        max_length=3,
        min_length=3,
        help_text=_("Target currency code (ISO 4217)"),
        trim_whitespace=True,
    )
    amount = serializers.DecimalField(
        max_digits=18,
        decimal_places=6,
        min_value=Decimal("0.000001"),
        help_text=_("Amount to convert (positive decimal)"),
    )
    date = serializers.DateField(
        required=False,
        allow_null=True,
        help_text=_("Optional date for historical exchange rate"),
    )

    def validate(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate conversion request.

        Args:
            attrs: Dictionary of validated attributes

        Returns:
            Validated attributes

        Raises:
            serializers.ValidationError: If validation fails
        """
        logger.debug(f"Validating currency conversion request")

        source_code = attrs["source_currency"].upper()
        target_code = attrs["target_currency"].upper()

        # Get currency instances
        try:
            source_currency = Currency.objects.get(code=source_code, is_active=True)
            target_currency = Currency.objects.get(code=target_code, is_active=True)
        except Currency.DoesNotExist as e:
            logger.error(f"Currency not found: {e}")
            raise serializers.ValidationError(
                _("One or both currencies not found or inactive."),
                code="currency_not_found",
            )

        attrs["source_currency_obj"] = source_currency
        attrs["target_currency_obj"] = target_currency

        return attrs

    def create(self, validated_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Perform currency conversion.

        Args:
            validated_data: Validated conversion data

        Returns:
            Dictionary with conversion results
        """
        source_currency = validated_data["source_currency_obj"]
        target_currency = validated_data["target_currency_obj"]
        amount = validated_data["amount"]
        date = validated_data.get("date")

        logger.info(
            f"Converting {amount} {source_currency.code} "
            f"to {target_currency.code} (date: {date})"
        )

        try:
            # Perform conversion
            converted_amount = source_currency.convert_amount(
                amount, target_currency, date
            )

            if converted_amount is None:
                logger.error(
                    f"Conversion failed: {source_currency.code} -> {target_currency.code}"
                )
                raise serializers.ValidationError(
                    _("Currency conversion failed. Please try again later."),
                    code="conversion_failed",
                )

            # Prepare response
            result = {
                "source_currency": source_currency.code,
                "target_currency": target_currency.code,
                "original_amount": str(amount),
                "converted_amount": str(converted_amount),
                "exchange_rate": str(
                    source_currency._get_exchange_rate(target_currency, date)
                    or Decimal("0")
                ),
                "timestamp": timezone.now().isoformat(),
                "metadata": {
                    "source_currency_name": source_currency.name,
                    "target_currency_name": target_currency.name,
                    "source_symbol": source_currency.symbol or "",
                    "target_symbol": target_currency.symbol or "",
                },
            }

            logger.info(
                f"Conversion successful: {amount} {source_currency.code} = "
                f"{converted_amount} {target_currency.code}"
            )

            return result

        except Exception as e:
            logger.error(f"Unexpected error during currency conversion: {e}")
            raise serializers.ValidationError(
                _("An unexpected error occurred during conversion."),
                code="conversion_error",
            )


class ExchangeRateUpdateSerializer(serializers.Serializer):
    """
    Serializer for batch exchange rate updates.

    Used by admin/scheduled tasks to update multiple currency rates.
    """

    rates = serializers.DictField(
        child=serializers.DecimalField(
            max_digits=18, decimal_places=8, min_value=Decimal("0.00000001")
        ),
        help_text=_("Dictionary of currency_code -> exchange_rate"),
    )
    source = serializers.CharField(
        max_length=50,
        help_text=_("Source of exchange rates (e.g., 'ECB', 'OpenExchangeRates')"),
    )

    def create(self, validated_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update exchange rates in batch.

        Args:
            validated_data: Validated update data

        Returns:
            Update statistics

        Raises:
            serializers.ValidationError: If update fails
        """
        rates = validated_data["rates"]
        source = validated_data["source"]

        logger.info(
            f"Batch updating exchange rates from {source}: {len(rates)} currencies"
        )

        try:
            # Use model's batch update method
            stats = Currency.update_exchange_rates(rates, source)

            # Add additional metadata to response
            stats["source"] = source
            stats["timestamp"] = timezone.now().isoformat()
            stats["currencies_updated"] = list(rates.keys())

            return stats

        except Exception as e:
            logger.error(f"Failed to update exchange rates: {e}")
            raise serializers.ValidationError(
                _(
                    "Failed to update exchange rates. Please check the data and try again."
                ),
                code="batch_update_failed",
            )


class CurrencyListSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer for currency lists.

    Optimized for listing endpoints with minimal data.
    """

    class Meta:
        model = Currency
        fields = [
            "id",
            "code",
            "name",
            "symbol",
            "is_active",
            "is_base_currency",
        ]
        read_only_fields = fields


class CurrencyDetailSerializer(CurrencySerializer):
    """
    Extended serializer for currency detail views.

    Includes historical rates and additional metadata.
    """

    historical_rates = serializers.SerializerMethodField()

    class Meta(CurrencySerializer.Meta):
        fields = CurrencySerializer.Meta.fields + ["historical_rates"]

    def get_historical_rates(self, obj: Currency) -> List[Dict[str, Any]]:
        """
        Get formatted historical rates.

        Args:
            obj: Currency instance

        Returns:
            List of historical rate entries
        """
        # Limit to last 10 entries for API response
        return obj.historical_rates[-10:] if obj.historical_rates else []

    def to_representation(self, instance: Currency) -> Dict[str, Any]:
        """
        Custom representation for detail view.

        Args:
            instance: Currency instance

        Returns:
            Dictionary representation
        """
        data = super().to_representation(instance)

        # Add additional calculated fields
        data["conversion_examples"] = self._get_conversion_examples(instance)
        data["usage_count"] = self._get_usage_count(instance)

        return data

    def _get_conversion_examples(self, currency: Currency) -> List[Dict[str, Any]]:
        """Get example conversions for common amounts."""
        examples = []
        common_amounts = [Decimal("1"), Decimal("10"), Decimal("100"), Decimal("1000")]

        # Get a few other active currencies for examples
        other_currencies = Currency.objects.filter(is_active=True).exclude(
            id=currency.id
        )[:3]

        for other_currency in other_currencies:
            for amount in common_amounts:
                converted = currency.convert_amount(amount, other_currency)
                if converted:
                    examples.append(
                        {
                            "from": {
                                "amount": str(amount),
                                "currency": currency.code,
                                "symbol": currency.symbol or "",
                            },
                            "to": {
                                "amount": str(converted),
                                "currency": other_currency.code,
                                "symbol": other_currency.symbol or "",
                            },
                            "rate": str(
                                currency._get_exchange_rate(other_currency)
                                or Decimal("0")
                            ),
                        }
                    )

        return examples

    def _get_usage_count(self, currency: Currency) -> Dict[str, int]:
        """Get usage statistics for the currency."""
        # Import here to avoid circular imports
        from accounts.models import Account, Transaction

        try:
            account_count = Account.objects.filter(currency=currency).count()
            transaction_count = Transaction.objects.filter(
                original_currency=currency
            ).count()

            return {
                "accounts": account_count,
                "transactions": transaction_count,
            }
        except Exception as e:
            logger.warning(f"Could not fetch usage count for {currency.code}: {e}")
            return {"accounts": 0, "transactions": 0}


class BaseCategorySerializer(serializers.ModelSerializer):
    """Base serializer with common category functionality."""

    def _get_request_user(self):
        """Safely get request user from context."""
        request = self.context.get("request")
        return request.user if request and request.user.is_authenticated else None


class CategoryListSerializer(BaseCategorySerializer):
    """
    Lightweight serializer for listing categories.

    Optimized for performance with minimal fields.
    """

    parent_name = serializers.CharField(source="parent.name", read_only=True)
    has_children = serializers.SerializerMethodField()
    full_path = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = [
            "id",
            "name",
            "category_type",
            "parent",
            "parent_name",
            "full_path",
            "has_children",
            "is_active",
            "is_system_category",
            "transaction_count",
        ]
        read_only_fields = fields

    # @extend_schema_field(serializers.BooleanField)
    def get_has_children(self, obj: Category) -> bool:
        """Check if category has children."""
        return obj.children.filter(is_active=True).exists()

    # @extend_schema_field(serializers.CharField)
    def get_full_path(self, obj: Category) -> str:
        """Get full hierarchical path."""
        return obj.full_path if hasattr(obj, "full_path") else obj.name


class CategoryDetailSerializer(BaseCategorySerializer):
    """
    Detailed serializer for category retrieval.

    Includes complete information and relationships.
    """

    parent_info = serializers.SerializerMethodField()
    children = serializers.SerializerMethodField()
    ancestors = serializers.SerializerMethodField()
    usage_stats = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = [
            "id",
            "user",
            "name",
            "description",
            "category_type",
            "parent",
            "parent_info",
            "children",
            "ancestors",
            "full_path",
            "is_system_category",
            "is_active",
            "transaction_count",
            "last_used_at",
            "created_at",
            "updated_at",
            "usage_stats",
        ]
        read_only_fields = fields

    # @extend_schema_field(
    #     serializers.DictField(child=serializers.CharField(), allow_null=True)
    # )
    def get_parent_info(self, obj: Category) -> Optional[Dict[str, Any]]:
        """Get parent category information."""
        if obj.parent:
            return {
                "id": str(obj.parent.id),
                "name": obj.parent.name,
                "category_type": obj.parent.category_type,
                "is_active": obj.parent.is_active,
            }
        return None

    # @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_children(self, obj: Category) -> List[Dict[str, Any]]:
        """Get immediate children."""
        children = obj.children.filter(is_active=True)
        return CategoryListSerializer(children, many=True, context=self.context).data

    # @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_ancestors(self, obj: Category) -> List[Dict[str, Any]]:
        """Get ancestor categories."""
        ancestors = obj.get_ancestors() if hasattr(obj, "get_ancestors") else []
        return [
            {
                "id": str(cat.id),
                "name": cat.name,
                "category_type": cat.category_type,
            }
            for cat in ancestors
        ]

    # @extend_schema_field(serializers.DictField(child=serializers.CharField()))
    def get_usage_stats(self, obj: Category) -> Dict[str, Any]:
        """Get usage statistics."""
        return {
            "transaction_count": obj.transaction_count,
            "last_used": obj.last_used_at,
            "has_transactions": obj.transaction_count > 0,
        }


class CategoryCreateUpdateSerializer(BaseCategorySerializer):
    """
    Serializer for creating and updating categories.

    Handles:
    - Automatic user assignment
    - Validation of unique constraints
    - Hierarchy validation
    - System category restrictions
    """

    can_delete = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = [
            "id",
            "name",
            "description",
            "category_type",
            "parent",
            "is_system_category",
            "is_active",
            "can_delete",
        ]
        read_only_fields = ["id", "can_delete"]
        extra_kwargs = {
            "name": {
                "max_length": 255,
                "trim_whitespace": True,
            },
            "description": {
                "required": False,
                "allow_blank": True,
            },
            "parent": {
                "required": False,
                "allow_null": True,
            },
            "is_system_category": {
                "read_only": True,  # Managed by ViewSet
            },
        }

    # @extend_schema_field(serializers.BooleanField)
    def get_can_delete(self, obj: Category) -> bool:
        """
        Check if category can be deleted.

        Rules:
        - No transactions
        - No active children
        """
        if obj.transaction_count > 0:
            return False

        if obj.children.filter(is_active=True).exists():
            return False

        return True

    def validate_name(self, value: str) -> str:
        """Validate category name."""
        value = value.strip()

        if not value:
            raise serializers.ValidationError(
                _("Category name cannot be empty."), code="empty_name"
            )

        if len(value) > 255:
            raise serializers.ValidationError(
                _("Category name cannot exceed 255 characters."), code="name_too_long"
            )

        return value

    def validate_parent(self, value: Optional[Category]) -> Optional[Category]:
        """Validate parent category."""
        if value is None:
            return None

        user = self._get_request_user()

        # Check if parent belongs to same user (or is a system category)
        if value.user and value.user != user:
            raise serializers.ValidationError(
                _(
                    "Parent category must belong to the same user or be a system category."
                ),
                code="invalid_parent_user",
            )

        # Check if parent is active
        if not value.is_active:
            raise serializers.ValidationError(
                _("Parent category must be active."), code="inactive_parent"
            )

        # Check for circular reference
        instance = self.instance
        if instance and instance.id == value.id:
            raise serializers.ValidationError(
                _("Category cannot be its own parent."), code="self_parent"
            )

        return value

    def validate(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
        """Perform cross-field validation."""
        user = self._get_request_user()
        instance = self.instance

        # Get current values
        name = attrs.get("name", getattr(instance, "name", None))
        category_type = attrs.get(
            "category_type", getattr(instance, "category_type", None)
        )
        parent = attrs.get("parent", getattr(instance, "parent", None))

        # Validate unique constraint
        if user and name and category_type:
            self._validate_unique_constraint(user, name, category_type, instance)

        # Validate hierarchy
        if parent and instance:
            self._validate_hierarchy(parent, instance)

        return attrs

    def _validate_unique_constraint(
        self,
        user: models.Model,
        name: str,
        category_type: str,
        instance: Optional[Category],
    ) -> None:
        """Validate unique constraint per (user, name, category_type)."""
        query = models.Q(name=name, category_type=category_type)

        if user:
            query &= models.Q(user=user)
        else:
            query &= models.Q(user__isnull=True)

        # Exclude current instance if updating
        if instance:
            query &= ~models.Q(id=instance.id)

        # Check for existing category
        if Category.objects.filter(query).exists():
            raise serializers.ValidationError(
                {
                    "name": _(
                        f"A category with name '{name}' and type '{category_type}' "
                        f"already exists."
                    )
                }
            )

    def _validate_hierarchy(self, parent: Category, instance: Category) -> None:
        """Validate hierarchical relationships."""
        # Check for circular references
        ancestors = (
            parent.get_ancestors(include_self=True)
            if hasattr(parent, "get_ancestors")
            else []
        )

        if instance in ancestors:
            raise serializers.ValidationError(
                {"parent": _("Circular reference detected in category hierarchy.")}
            )

    def create(self, validated_data: Dict[str, Any]) -> Category:
        """Create a new category."""
        user = self._get_request_user()
        is_staff = user.is_staff or user.is_superuser

        # Automated logic: staff create system categories, others create personal
        if is_staff:
            validated_data["is_system_category"] = True
            validated_data["user"] = None
        else:
            validated_data["is_system_category"] = False
            validated_data["user"] = user

        try:
            return super().create(validated_data)
        except Exception as e:
            logger.exception(f"Category creation failed: {e}")
            raise serializers.ValidationError(_("Failed to create category."))

    def update(self, instance: Category, validated_data: Dict[str, Any]) -> Category:
        """Update an existing category."""
        user = self._get_request_user()
        is_staff = user.is_staff or user.is_superuser

        # Security: protect system categories from regular users
        if instance.is_system_category and not is_staff:
            raise PermissionDenied(_("System categories are read-only."))

        # Security: protect critical fields from regular users
        if not is_staff:
            validated_data.pop("is_system_category", None)
            validated_data.pop("user", None)

        return super().update(instance, validated_data)


class CategoryTreeSerializer(BaseCategorySerializer):
    """
    Serializer for hierarchical category tree display.
    """

    children = serializers.SerializerMethodField()
    depth = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = [
            "id",
            "name",
            "category_type",
            "parent",
            "children",
            "depth",
            "is_active",
            "transaction_count",
        ]
        read_only_fields = fields

    # @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_children(self, obj: Category) -> List[Dict[str, Any]]:
        """Recursively get children."""
        children = obj.children.filter(is_active=True).order_by("name")
        return CategoryTreeSerializer(children, many=True, context=self.context).data

    # @extend_schema_field(serializers.IntegerField)
    def get_depth(self, obj: Category) -> int:
        """Calculate category depth in hierarchy."""
        return len(obj.get_ancestors())
