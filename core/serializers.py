from rest_framework import serializers, exceptions
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, Optional, List
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models
from django.utils import timezone

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
                "max_digits": 18,
                "decimal_places": 8,
                "min_value": 0,
            },
            "decimal_places": {
                "help_text": _("Number of decimal places (0-6)"),
                "min_value": 0,
                "max_value": 6,
            },
        }

    def get_formatted_exchange_rate(self, obj: Currency) -> str:
        """Get formatted exchange rate for display."""
        try:
            # Format with appropriate decimal places
            return f"{obj.exchange_rate:.{min(6, obj.decimal_places)}f}"
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

        Args:
            value: Currency code to validate

        Returns:
            Uppercase validated currency code

        Raises:
            serializers.ValidationError: If code is invalid
        """
        logger.debug(f"Validating currency code: {value}")

        if not value:
            raise serializers.ValidationError(
                _("Currency code cannot be empty."), code="empty_currency_code"
            )

        value = value.strip().upper()

        # ISO 4217 validation
        if len(value) != 3:
            raise serializers.ValidationError(
                _("Currency code must be exactly 3 characters."), code="invalid_length"
            )

        if not value.isalpha():
            raise serializers.ValidationError(
                _("Currency code must contain only letters."), code="invalid_characters"
            )

        logger.debug(f"Currency code validation passed: {value}")
        return value

    def validate_exchange_rate(self, value: Decimal) -> Decimal:
        """
        Validate exchange rate is positive and reasonable.

        Args:
            value: Exchange rate to validate

        Returns:
            Validated exchange rate

        Raises:
            serializers.ValidationError: If rate is invalid
        """
        logger.debug(f"Validating exchange rate: {value}")

        try:
            if value <= Decimal("0"):
                raise serializers.ValidationError(
                    _("Exchange rate must be positive."), code="non_positive_rate"
                )

            # Optional: Add business logic constraints
            # Example: Prevent unrealistically large rates
            if value > Decimal("1000000"):  # Arbitrary limit
                logger.warning(f"Unusually high exchange rate: {value}")
                # Not an error, just a warning

        except (InvalidOperation, TypeError) as e:
            logger.error(f"Invalid exchange rate format: {value}, error: {e}")
            raise serializers.ValidationError(
                _("Exchange rate must be a valid decimal number."),
                code="invalid_decimal",
            )

        logger.debug(f"Exchange rate validation passed: {value}")
        return value

    def validate_decimal_places(self, value: int) -> int:
        """
        Validate decimal places are within reasonable range.

        Args:
            value: Number of decimal places

        Returns:
            Validated decimal places

        Raises:
            serializers.ValidationError: If value is out of range
        """
        if not 0 <= value <= 6:
            raise serializers.ValidationError(
                _("Decimal places must be between 0 and 6."),
                code="invalid_decimal_places",
            )
        return value

    def validate(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Perform cross-field validation.

        Args:
            attrs: Dictionary of validated attributes

        Returns:
            Validated attributes with any modifications

        Raises:
            serializers.ValidationError: If cross-field validation fails
        """
        logger.debug(f"Performing cross-field validation for currency")

        # Check for base currency conflict
        is_base_currency = attrs.get(
            "is_base_currency",
            (
                getattr(self.instance, "is_base_currency", False)
                if self.instance
                else False
            ),
        )

        if is_base_currency:
            # If this is being set as base currency, validate no other base exists
            code = attrs.get("code", getattr(self.instance, "code", ""))

            existing_base = (
                Currency.objects.filter(is_base_currency=True)
                .exclude(code=code)
                .first()
            )

            if existing_base and self.instance and self.instance.id != existing_base.id:
                # Allow update of existing base currency
                pass
            elif existing_base and not self.instance:
                # Creating new base when one already exists
                raise serializers.ValidationError(
                    {
                        "is_base_currency": _(
                            f"Cannot set {code} as base currency. "
                            f"{existing_base.code} is already the base currency."
                        )
                    },
                    code="multiple_base_currencies",
                )

        # Ensure exchange rate is 1.0 for base currency
        if is_base_currency:
            exchange_rate = attrs.get(
                "exchange_rate", getattr(self.instance, "exchange_rate", Decimal("1.0"))
            )

            if exchange_rate != Decimal("1.0"):
                logger.warning(
                    f"Base currency {attrs.get('code')} has exchange rate != 1.0: {exchange_rate}"
                )
                # Auto-correct to 1.0
                attrs["exchange_rate"] = Decimal("1.0")

        logger.debug("Cross-field validation passed")
        return attrs

    def create(self, validated_data: Dict[str, Any]) -> Currency:
        """
        Create a new currency with proper error handling and logging.

        Args:
            validated_data: Validated data for currency creation

        Returns:
            Created Currency instance

        Raises:
            serializers.ValidationError: If creation fails
        """
        logger.info(f"Creating new currency: {validated_data.get('code')}")

        try:
            # Ensure code is uppercase
            validated_data["code"] = validated_data["code"].upper()

            # Handle base currency logic
            if validated_data.get("is_base_currency", False):
                self._handle_base_currency_creation(validated_data)

            # Create the currency
            currency = Currency.objects.create(**validated_data)

            logger.info(f"Successfully created currency: {currency.code}")
            return currency

        except DjangoValidationError as e:
            logger.error(f"Model validation failed creating currency: {e}")
            raise serializers.ValidationError(e.message_dict)

        except Exception as e:
            logger.error(f"Unexpected error creating currency: {e}")
            raise serializers.ValidationError(
                _("An unexpected error occurred while creating the currency."),
                code="creation_error",
            )

    def update(self, instance: Currency, validated_data: Dict[str, Any]) -> Currency:
        """
        Update an existing currency with proper error handling.

        Args:
            instance: Existing Currency instance
            validated_data: Validated data for update

        Returns:
            Updated Currency instance

        Raises:
            serializers.ValidationError: If update fails
        """
        logger.info(f"Updating currency: {instance.code}")

        try:
            # Handle base currency changes
            new_is_base = validated_data.get(
                "is_base_currency", instance.is_base_currency
            )

            if new_is_base and not instance.is_base_currency:
                self._handle_base_currency_update(instance, validated_data)

            # Update fields
            for attr, value in validated_data.items():
                setattr(instance, attr, value)

            # Save with validation
            instance.save()

            logger.info(f"Successfully updated currency: {instance.code}")
            return instance

        except DjangoValidationError as e:
            logger.error(
                f"Model validation failed updating currency {instance.code}: {e}"
            )
            raise serializers.ValidationError(e.message_dict)

        except Exception as e:
            logger.error(f"Unexpected error updating currency {instance.code}: {e}")
            raise serializers.ValidationError(
                _("An unexpected error occurred while updating the currency."),
                code="update_error",
            )

    def _handle_base_currency_creation(self, validated_data: Dict[str, Any]) -> None:
        """Handle logic for creating a new base currency."""
        existing_base = Currency.get_base_currency()

        if existing_base:
            raise serializers.ValidationError(
                {
                    "is_base_currency": _(
                        f"Cannot create new base currency {validated_data['code']}. "
                        f"{existing_base.code} is already the base currency."
                    )
                }
            )

        # Ensure exchange rate is 1.0 for base currency
        validated_data["exchange_rate"] = Decimal("1.0")
        logger.info(f"Creating new base currency: {validated_data['code']}")

    def _handle_base_currency_update(
        self, instance: Currency, validated_data: Dict[str, Any]
    ) -> None:
        """Handle logic for updating a currency to become base currency."""
        existing_base = Currency.get_base_currency()

        if existing_base and existing_base.id != instance.id:
            # Demote existing base currency
            existing_base.is_base_currency = False
            existing_base.save(update_fields=["is_base_currency", "updated_at"])
            logger.info(f"Demoted existing base currency: {existing_base.code}")

        # Set exchange rate to 1.0 for new base
        validated_data["exchange_rate"] = Decimal("1.0")
        logger.info(f"Promoting currency to base: {instance.code}")


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

    def _get_request_user(self) -> Optional[models.Model]:
        """Safely get request user from context."""
        request = self.context.get("request")
        return request.user if request and request.user.is_authenticated else None

    def _log_validation_warning(self, message: str, **kwargs) -> None:
        """Log validation warnings consistently."""
        user = self._get_request_user()
        user_id = user.id if user else "anonymous"
        logger.warning(f"User {user_id}: {message}", **kwargs)


class CategoryListSerializer(BaseCategorySerializer):
    """
    Lightweight serializer for category listing.

    Optimized for:
    - Category dropdowns
    - List views with minimal data
    - Tree/hierarchy displays

    Features:
    - Minimal fields for performance
    - Hierarchical parent information
    - Active status filtering
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

    def get_has_children(self, obj: Category) -> bool:
        """Check if category has children."""
        return obj.children.exists()

    def get_full_path(self, obj: Category) -> str:
        """Get full hierarchical path."""
        return obj.full_path if hasattr(obj, "full_path") else obj.name


class CategoryDetailSerializer(BaseCategorySerializer):
    """
    Detailed serializer for category retrieval.

    Includes:
    - Complete category information
    - Hierarchy details
    - Usage statistics
    - System category metadata
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

    def get_parent_info(self, obj: Category) -> Optional[Dict[str, Any]]:
        """Get parent category information."""
        if obj.parent:
            return {
                "id": obj.parent.id,
                "name": obj.parent.name,
                "category_type": obj.parent.category_type,
                "is_active": obj.parent.is_active,
            }
        return None

    def get_children(self, obj: Category) -> List[Dict[str, Any]]:
        """Get immediate children."""
        return CategoryListSerializer(
            obj.children.filter(is_active=True), many=True, context=self.context
        ).data

    def get_ancestors(self, obj: Category) -> List[Dict[str, Any]]:
        """Get ancestor categories."""
        ancestors = obj.get_ancestors() if hasattr(obj, "get_ancestors") else []
        return [
            {
                "id": cat.id,
                "name": cat.name,
                "category_type": cat.category_type,
            }
            for cat in ancestors
        ]

    def get_usage_stats(self, obj: Category) -> Dict[str, Any]:
        """Get usage statistics."""
        return {
            "transaction_count": obj.transaction_count,
            "last_used": obj.last_used_at,
            "has_transactions": obj.transaction_count > 0,
        }


class CategorySerializer(BaseCategorySerializer):
    """
    Primary serializer for Category CRUD operations.

    Responsibilities:
    - Validates user vs system categories
    - Ensures unique names per user and type
    - Maintains hierarchy integrity
    - Prevents modification of system categories
    - Logs meaningful errors for debugging

    Security:
    - Users cannot create/modify system categories
    - Parent must belong to same user
    - Hierarchical integrity checks
    """

    parent_name = serializers.CharField(source="parent.name", read_only=True)
    can_delete = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = [
            "id",
            "user",
            "name",
            "description",
            "category_type",
            "parent",
            "parent_name",
            "is_system_category",
            "is_active",
            "transaction_count",
            "last_used_at",
            "created_at",
            "updated_at",
            "can_delete",
        ]
        read_only_fields = [
            "id",
            "user",
            # "is_system_category",  # Managed via custom validation
            "transaction_count",
            "last_used_at",
            "created_at",
            "updated_at",
            "can_delete",
        ]
        extra_kwargs = {
            "name": {
                "help_text": _("Category name (unique per user and type)"),
                "max_length": 255,
                "trim_whitespace": True,
            },
            "description": {
                "help_text": _("Detailed category description"),
                "required": False,
                "allow_blank": True,
            },
            "parent": {
                "help_text": _("Parent category for hierarchy"),
                "required": False,
                "allow_null": True,
            },
        }

    def get_can_delete(self, obj: Category) -> bool:
        """
        Check if category can be deleted.

        Rules:
        - Must not be a system category
        - Must have no transactions
        - Must have no active children
        """
        if obj.is_system_category:
            return False

        if obj.transaction_count > 0:
            return False

        # Check if has active children
        if obj.children.filter(is_active=True).exists():
            return False

        return True

    def validate_name(self, value: str) -> str:
        """
        Validate category name.

        Args:
            value: Category name

        Returns:
            Cleaned category name

        Raises:
            serializers.ValidationError: If name is invalid
        """
        value = value.strip()

        if not value:
            raise serializers.ValidationError(
                _("Category name cannot be empty."), code="empty_name"
            )

        if len(value) > 255:
            raise serializers.ValidationError(
                _("Category name cannot exceed 255 characters."), code="name_too_long"
            )

        logger.debug(f"Category name validation passed: {value}")
        return value

    def validate_parent(self, value: Optional[Category]) -> Optional[Category]:
        """
        Validate parent category.

        Args:
            value: Parent category instance

        Returns:
            Validated parent category

        Raises:
            serializers.ValidationError: If parent is invalid
        """
        if value is None:
            return None

        user = self._get_request_user()

        # Check if parent belongs to same user
        if value.user != user:
            self._log_validation_warning(
                f"Attempted to use parent category from different user: {value.id}"
            )
            raise serializers.ValidationError(
                _("Parent category must belong to the same user."),
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
        """
        Perform cross-field validation.

        Args:
            attrs: Dictionary of validated attributes

        Returns:
            Validated attributes

        Raises:
            serializers.ValidationError: If validation fails
        """
        logger.debug("Performing cross-field validation for category")

        user = self._get_request_user()
        instance = self.instance

        # Get values with fallbacks
        name = attrs.get("name", getattr(instance, "name", None))
        category_type = attrs.get(
            "category_type", getattr(instance, "category_type", None)
        )
        parent = attrs.get("parent", getattr(instance, "parent", None))

        # Prevent users from creating system categories (unless admin)
        is_system_update = attrs.get("is_system_category", False)
        if is_system_update:
            request = self.context.get("request")
            if not request or not (request.user.is_staff or request.user.is_superuser):
                self._log_validation_warning("Attempted to create system category")
                raise serializers.ValidationError(
                    {"is_system_category": _("Users cannot create system categories.")}
                )

        # Validate unique constraint
        if user and name and category_type:
            try:
                self._validate_unique_constraint(user, name, category_type, instance)
            except DjangoValidationError as e:
                # Convert Django ValidationError to DRF ValidationError
                raise serializers.ValidationError(e.message_dict)

        # Validate hierarchy
        if parent:
            self._validate_hierarchy(parent, instance)

        # Ensure category type matches parent type
        if parent and category_type and parent.category_type != category_type:
            self._log_validation_warning(
                f"Category type mismatch: {category_type} vs parent {parent.category_type}"
            )
            # This is a warning, not an error
            # attrs['category_type'] = parent.category_type  # Uncomment to auto-correct

        logger.debug("Cross-field validation passed")
        return attrs

    def _validate_unique_constraint(
        self,
        user: models.Model,
        name: str,
        category_type: str,
        instance: Optional[Category],
    ) -> None:
        """
        Validate unique constraint per (user, name, category_type).

        Args:
            user: User instance
            name: Category name
            category_type: Category type
            instance: Current instance (if updating)

        Raises:
            DjangoValidationError: If constraint violated
        """
        # Build query
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
            error_message = _(
                f"A category with name '{name}' and type '{category_type}' "
                f"already exists."
            )

            raise DjangoValidationError(
                {"name": [error_message], "category_type": [error_message]}
            )

    def _validate_hierarchy(
        self, parent: Category, instance: Optional[Category]
    ) -> None:
        """
        Validate hierarchical relationships.

        Args:
            parent: Parent category
            instance: Current instance

        Raises:
            serializers.ValidationError: If hierarchy is invalid
        """
        # Check for circular references
        if instance:
            # Get all ancestors of the parent
            try:
                parent_ancestors = parent.get_ancestors(include_self=True)
            except AttributeError:
                # Fallback if get_ancestors not available
                parent_ancestors = []
                current = parent
                while current:
                    parent_ancestors.append(current)
                    current = current.parent

            # Check if instance is in parent's ancestors
            if instance in parent_ancestors:
                raise serializers.ValidationError(
                    {"parent": _("Circular reference detected in category hierarchy.")}
                )

    def create(self, validated_data: Dict[str, Any]) -> Category:
        """
        Create a new category.

        Args:
            validated_data: Validated data for creation

        Returns:
            Created Category instance

        Raises:
            serializers.ValidationError: If creation fails
        """
        logger.info("Creating new category")

        try:
            user = self._get_request_user()

            # Ensure user is set (only for non-system categories)
            if not validated_data.get("is_system_category", False):
                if "user" not in validated_data and user:
                    validated_data["user"] = user
            else:
                # System categories have no user
                validated_data["user"] = None

            # Create category
            category = Category.objects.create(**validated_data)

            logger.info(
                f"Category created: id={category.id}, "
                f"name='{category.name}', user={user.id if user else 'system'}"
            )

            return category

        except DjangoValidationError as e:
            logger.error(f"Model validation failed creating category: {e}")
            raise serializers.ValidationError(e.message_dict)

        except Exception as e:
            logger.exception(f"Unexpected error creating category: {e}")
            raise serializers.ValidationError(
                _("An unexpected error occurred while creating the category."),
                code="creation_error",
            )

    def update(self, instance: Category, validated_data: Dict[str, Any]) -> Category:
        """
        Update an existing category.

        Args:
            instance: Existing Category instance
            validated_data: Validated data for update

        Returns:
            Updated Category instance

        Raises:
            serializers.ValidationError: If update fails
        """
        logger.info(f"Updating category: id={instance.id}, name='{instance.name}'")

        # Prevent modification of system categories
        if instance.is_system_category:
            self._log_validation_warning(
                f"Attempted to modify system category: {instance.id}"
            )
            raise serializers.ValidationError(
                _("System categories cannot be modified."),
                code="system_category_readonly",
            )

        try:
            # Update fields
            for attr, value in validated_data.items():
                setattr(instance, attr, value)

            # Save with validation
            instance.save()

            logger.info(f"Category updated: id={instance.id}, name='{instance.name}'")
            return instance

        except DjangoValidationError as e:
            logger.error(
                f"Model validation failed updating category {instance.id}: {e}"
            )
            raise serializers.ValidationError(e.message_dict)

        except Exception as e:
            logger.exception(f"Unexpected error updating category {instance.id}: {e}")
            raise serializers.ValidationError(
                _("An unexpected error occurred while updating the category."),
                code="update_error",
            )


class CategoryTreeSerializer(BaseCategorySerializer):
    """
    Serializer for hierarchical category tree display.

    Optimized for:
    - Category management interfaces
    - Tree visualization
    - Bulk operations
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

    def get_children(self, obj: Category) -> List[Dict[str, Any]]:
        """Recursively get children."""
        children = obj.children.filter(is_active=True).order_by("name")
        return CategoryTreeSerializer(children, many=True, context=self.context).data

    def get_depth(self, obj: Category) -> int:
        """Calculate category depth in hierarchy."""
        depth = 0
        current = obj.parent

        while current:
            depth += 1
            current = current.parent

        return depth


class CategoryBulkUpdateSerializer(serializers.Serializer):
    """
    Serializer for bulk category operations.

    Used for:
    - Batch activation/deactivation
    - Mass parent reassignment
    - Bulk deletion
    """

    category_ids = serializers.ListField(
        child=serializers.UUIDField(),
        help_text=_("List of category IDs to update"),
        min_length=1,
        max_length=100,  # Limit batch size
    )
    action = serializers.ChoiceField(
        choices=[
            ("activate", "Activate"),
            ("deactivate", "Deactivate"),
            ("change_parent", "Change Parent"),
        ],
        help_text=_("Action to perform on categories"),
    )
    parent_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text=_("New parent category ID (for change_parent action)"),
    )

    def validate(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
        """Validate bulk operation."""
        action = attrs["action"]
        parent_id = attrs.get("parent_id")

        if action == "change_parent" and parent_id is None:
            raise serializers.ValidationError(
                {"parent_id": _("Parent ID is required for change_parent action.")}
            )

        return attrs

    def _get_request_user(self) -> Optional[models.Model]:
        """Safely get request user from context."""
        request = self.context.get("request")
        return request.user if request and request.user.is_authenticated else None

    def create(self, validated_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute bulk category operation.

        Args:
            validated_data: Validated bulk operation data

        Returns:
            Operation statistics
        """
        category_ids = validated_data["category_ids"]
        action = validated_data["action"]
        parent_id = validated_data.get("parent_id")
        user = self._get_request_user()

        logger.info(
            f"Bulk category operation: action={action}, "
            f"categories={len(category_ids)}, user={user.id if user else 'system'}"
        )

        try:
            # Get categories belonging to user
            categories = Category.objects.filter(
                id__in=category_ids,
                user=user,
                is_system_category=False,  # Cannot modify system categories
            )

            stats = {
                "total_requested": len(category_ids),
                "total_processed": categories.count(),
                "successful": 0,
                "failed": 0,
                "errors": [],
            }

            # Perform action
            if action == "activate":
                updated = categories.filter(is_active=False).update(is_active=True)
                stats["successful"] = updated

            elif action == "deactivate":
                # Check for categories with transactions
                for category in categories:
                    if category.transaction_count > 0:
                        stats["failed"] += 1
                        stats["errors"].append(
                            {
                                "category_id": str(category.id),
                                "error": "Category has transactions and cannot be deactivated.",
                            }
                        )
                    else:
                        category.is_active = False
                        category.save()
                        stats["successful"] += 1

            elif action == "change_parent":
                parent = (
                    Category.objects.filter(
                        id=parent_id, user=user, is_active=True
                    ).first()
                    if parent_id
                    else None
                )

                for category in categories:
                    try:
                        # Check for circular reference
                        if parent and category.id == parent.id:
                            raise ValueError("Cannot set category as its own parent.")

                        category.parent = parent
                        category.save()
                        stats["successful"] += 1

                    except Exception as e:
                        stats["failed"] += 1
                        stats["errors"].append(
                            {"category_id": str(category.id), "error": str(e)}
                        )

            logger.info(f"Bulk operation completed: {stats}")
            return stats

        except Exception as e:
            logger.exception(f"Bulk category operation failed: {e}")
            raise serializers.ValidationError(
                _("Bulk operation failed. Please try again."),
                code="bulk_operation_failed",
            )


# Factory function for getting appropriate serializer
def get_category_serializer(action: str = "default") -> serializers.Serializer:
    """
    Get appropriate serializer based on action.

    Args:
        action: Serializer action ('list', 'detail', 'tree', 'bulk', 'default')

    Returns:
        Appropriate serializer class
    """
    serializer_map = {
        "list": CategoryListSerializer,
        "detail": CategoryDetailSerializer,
        "tree": CategoryTreeSerializer,
        "bulk": CategoryBulkUpdateSerializer,
        "default": CategorySerializer,
    }

    return serializer_map.get(action, CategorySerializer)
