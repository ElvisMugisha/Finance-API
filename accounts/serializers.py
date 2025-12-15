from datetime import date
from typing import Dict, Any, Optional, List
from decimal import Decimal, InvalidOperation

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers, exceptions
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from django.db import transaction as db_transaction

from core.models import Category, Currency
from core.serializers import CurrencySerializer
from utils import choices, loggings

from .models import Account, Transaction

logger = loggings.setup_logging()


class BaseAccountSerializer(serializers.ModelSerializer):
    """Base serializer with common account functionality."""

    def _get_request_user(self):
        """Safely get request user from context."""
        request = self.context.get("request")
        return request.user if request and request.user.is_authenticated else None

    def _log_validation_warning(self, message: str, **kwargs) -> None:
        """Log validation warnings consistently."""
        user = self._get_request_user()
        user_id = user.id if user else "anonymous"
        logger.warning(f"User {user_id}: {message}", **kwargs)

    def _validate_balance(self, field_name: str, value: Decimal) -> Decimal:
        """Common balance validation logic."""
        try:
            # Ensure it's a valid Decimal
            if not isinstance(value, Decimal):
                value = Decimal(str(value))

            # Check reasonable bounds (prevent extreme values)
            min_limit = Decimal("-1000000000")  # -1 billion
            max_limit = Decimal("1000000000")  # +1 billion

            if value < min_limit or value > max_limit:
                self._log_validation_warning(f"Unusual {field_name} value: {value}")
                # Still allow, but log it

            return value

        except (InvalidOperation, TypeError, ValueError) as e:
            logger.error(f"Invalid {field_name} value: {value}, error: {e}")
            raise serializers.ValidationError(
                _(
                    f"{field_name.replace('_', ' ').title()} must be a valid decimal number."
                ),
                code=f"invalid_{field_name}",
            )


class AccountSerializer(BaseAccountSerializer):
    """
    Primary serializer for Account CRUD operations.

    Responsibilities:
    - Handles creation and updates of financial accounts
    - Validates account type-specific rules
    - Manages currency relationships
    - Ensures business logic (primary account, balances, etc.)
    - Prevents unauthorized modifications

    Security:
    - Users can only manage their own accounts
    - Currency must be active
    - Balance updates have validation

    Performance:
    - Optimized database queries
    - Minimal field selection for writes
    - Proper nested serialization for reads
    """

    # Currency handling: write with ID, read with full details
    currency_id = serializers.PrimaryKeyRelatedField(
        queryset=Currency.objects.filter(is_active=True),
        source="currency",
        write_only=True,
        help_text=_("ID of the currency for this account"),
    )
    currency = serializers.SerializerMethodField(read_only=True)

    # Read-only calculated fields
    formatted_balance = serializers.SerializerMethodField()
    available_balance = serializers.SerializerMethodField()
    can_be_primary = serializers.SerializerMethodField()

    class Meta:
        model = Account
        fields = [
            "id",
            "name",
            "account_type",
            "account_number",
            "bank_name",
            "bank_code",
            "currency_id",
            "currency",
            "initial_balance",
            "current_balance",
            "reconciled_balance",
            "reconciled_at",
            "formatted_balance",
            "available_balance",
            "is_primary",
            "is_active",
            "is_locked",
            "institution_data",
            "last_synced_at",
            "balance_updated_at",
            "can_be_primary",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "current_balance",  # Updated via transactions only
            "reconciled_balance",
            "reconciled_at",
            "formatted_balance",
            "available_balance",
            "can_be_primary",
            "last_synced_at",
            "balance_updated_at",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            "name": {
                "help_text": _('Account name (e.g., "Main Checking", "Savings")'),
                "max_length": 255,
                "trim_whitespace": True,
            },
            "account_number": {
                "help_text": _("Bank account number or identifier"),
                "required": False,
                "allow_blank": True,
                "allow_null": True,
            },
            "bank_name": {
                "help_text": _("Name of the bank or institution"),
                "required": False,
                "allow_blank": True,
                "allow_null": True,
            },
            "initial_balance": {
                "help_text": _("Starting balance when account was created"),
                "max_digits": 18,
                "decimal_places": 2,
            },
            "is_primary": {
                "help_text": _("Whether this is the user's primary account"),
            },
            "institution_data": {
                "help_text": _("Additional data for bank integrations"),
                "required": False,
                "allow_null": True,
            },
        }

    def get_currency(self, obj: Account) -> Dict[str, Any]:
        """Get currency details for read operations."""
        from .serializers import CurrencyListSerializer  # Avoid circular import

        return CurrencyListSerializer(obj.currency).data

    def get_formatted_balance(self, obj: Account) -> str:
        """Get formatted balance with currency symbol."""
        return obj.formatted_balance if hasattr(obj, "formatted_balance") else ""

    def get_available_balance(self, obj: Account) -> Decimal:
        """Get available balance (same as current_balance for now)."""
        return obj.current_balance

    def get_can_be_primary(self, obj: Account) -> bool:
        """Check if account can be set as primary."""
        if not obj.is_active:
            return False

        if obj.is_locked:
            return False

        # Additional business logic can be added here
        return True

    def validate_name(self, value: str) -> str:
        """Validate account name."""
        value = value.strip()

        if not value:
            raise serializers.ValidationError(
                _("Account name cannot be empty."), code="empty_name"
            )

        if len(value) > 255:
            raise serializers.ValidationError(
                _("Account name cannot exceed 255 characters."), code="name_too_long"
            )

        # Check for uniqueness per user (handled in model, but validate early)
        user = self._get_request_user()
        if user and self.instance is None:  # Only on creation
            if Account.objects.filter(user=user, name=value).exists():
                raise serializers.ValidationError(
                    _("An account with this name already exists."),
                    code="duplicate_name",
                )

        logger.debug(f"Account name validation passed: {value}")
        return value

    def validate_account_type(self, value: str) -> str:
        """Validate account type."""
        valid_types = [choice[0] for choice in choices.AccountType.choices]

        if value not in valid_types:
            raise serializers.ValidationError(
                _(f'Invalid account type. Must be one of: {", ".join(valid_types)}'),
                code="invalid_account_type",
            )

        return value

    def validate_initial_balance(self, value: Decimal) -> Decimal:
        """Validate initial balance."""
        return self._validate_balance("initial_balance", value)

    def validate_current_balance(self, value: Decimal) -> Decimal:
        """Validate current balance (read-only, but validate if provided)."""
        return self._validate_balance("current_balance", value)

    def validate_account_number(self, value: Optional[str]) -> Optional[str]:
        """Validate account number based on account type."""
        if not value:
            return value

        value = value.strip()

        # Basic validation (can be extended based on country/bank)
        if len(value) > 100:
            raise serializers.ValidationError(
                _("Account number cannot exceed 100 characters."),
                code="account_number_too_long",
            )

        # TODO: Add format validation based on account_type/country
        # Example: IBAN validation, account number patterns, etc.

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
        logger.debug("Performing cross-field validation for account")

        # Get values with fallbacks
        account_type = attrs.get(
            "account_type", getattr(self.instance, "account_type", None)
        )
        account_number = attrs.get(
            "account_number", getattr(self.instance, "account_number", None)
        )
        bank_name = attrs.get("bank_name", getattr(self.instance, "bank_name", None))
        is_primary = attrs.get(
            "is_primary", getattr(self.instance, "is_primary", False)
        )
        is_active = attrs.get("is_active", getattr(self.instance, "is_active", True))

        # Validate account type specific rules
        if account_type == choices.AccountType.BANK:
            # Bank accounts should have account number and bank name
            if not account_number:
                self._log_validation_warning("Bank account missing account number")
                # Not an error, just a warning

            if not bank_name:
                self._log_validation_warning("Bank account missing bank name")
                # Not an error, just a warning

        elif account_type in [choices.AccountType.CASH, choices.AccountType.WALLET]:
            # Cash/Wallet accounts shouldn't have bank details
            if account_number or bank_name:
                logger.info(f"Clearing bank details for {account_type} account")
                attrs["account_number"] = None
                attrs["bank_name"] = None
                attrs["bank_code"] = None

        # Validate primary account logic
        if is_primary and not is_active:
            raise serializers.ValidationError(
                {"is_primary": _("Primary account must be active.")}
            )

        # Check currency is active (handled by PrimaryKeyRelatedField)

        logger.debug("Cross-field validation passed")
        return attrs

    def create(self, validated_data: Dict[str, Any]) -> Account:
        """
        Create a new account with business logic.

        Args:
            validated_data: Validated data for account creation

        Returns:
            Created Account instance

        Raises:
            serializers.ValidationError: If creation fails
        """
        logger.info("Creating new account")

        try:
            user = self._get_request_user()

            if not user:
                raise serializers.ValidationError(
                    _("User must be authenticated to create an account."),
                    code="unauthenticated",
                )

            # Set user and initial current balance
            validated_data["user"] = user
            validated_data["current_balance"] = validated_data.get(
                "initial_balance", Decimal("0.00")
            )

            # Handle primary account logic
            is_primary = validated_data.get("is_primary", False)
            if is_primary:
                self._handle_primary_account_logic(user, validated_data)

            # Create account within transaction
            with db_transaction.atomic():
                account = Account.objects.create(**validated_data)

            logger.info(
                f"Account created: id={account.id}, "
                f"name='{account.name}', "
                f"user={user.id}, "
                f"type={account.account_type}, "
                f"currency={account.currency.code}"
            )

            return account

        except DjangoValidationError as e:
            logger.error(f"Model validation failed creating account: {e}")
            raise serializers.ValidationError(e.message_dict)

        except Exception as e:
            logger.exception(f"Unexpected error creating account: {e}")
            raise serializers.ValidationError(
                _("An unexpected error occurred while creating the account."),
                code="creation_error",
            )

    def update(self, instance: Account, validated_data: Dict[str, Any]) -> Account:
        """
        Update an existing account.

        Args:
            instance: Existing Account instance
            validated_data: Validated data for update

        Returns:
            Updated Account instance

        Raises:
            serializers.ValidationError: If update fails
        """
        logger.info(f"Updating account: id={instance.id}, name='{instance.name}'")

        try:
            user = self._get_request_user()

            # Check if user owns this account
            if instance.user != user:
                self._log_validation_warning(
                    f"User {user.id} attempted to update another user's account {instance.id}"
                )
                raise serializers.ValidationError(
                    _("Cannot update another user's account."), code="unauthorized"
                )

            # Check if account is locked
            if instance.is_locked:
                raise serializers.ValidationError(
                    _("Cannot update a locked account."), code="account_locked"
                )

            # Handle initial balance changes
            if "initial_balance" in validated_data:
                new_initial = validated_data["initial_balance"]
                if new_initial != instance.initial_balance:
                    # Adjust current balance by the difference
                    diff = new_initial - instance.initial_balance
                    validated_data["current_balance"] = instance.current_balance + diff

                    logger.info(
                        f"Adjusting current balance by {diff} "
                        f"due to initial balance update"
                    )

            # Handle primary account logic
            is_primary = validated_data.get("is_primary", instance.is_primary)
            if is_primary and not instance.is_primary:
                self._handle_primary_account_logic(user, validated_data, instance)

            # Update fields
            for attr, value in validated_data.items():
                setattr(instance, attr, value)

            # Save with validation
            instance.save()

            logger.info(
                f"Account updated: id={instance.id}, "
                f"updated fields={list(validated_data.keys())}"
            )

            return instance

        except DjangoValidationError as e:
            logger.error(f"Model validation failed updating account {instance.id}: {e}")
            raise serializers.ValidationError(e.message_dict)

        except Exception as e:
            logger.exception(f"Unexpected error updating account {instance.id}: {e}")
            raise serializers.ValidationError(
                _("An unexpected error occurred while updating the account."),
                code="update_error",
            )

    def _handle_primary_account_logic(
        self, user, validated_data: Dict[str, Any], instance: Optional[Account] = None
    ) -> None:
        """
        Handle primary account logic when creating or updating.

        Args:
            user: User instance
            validated_data: Validated data
            instance: Existing instance (if updating)
        """
        try:
            with db_transaction.atomic():
                # Get current primary account
                current_primary = (
                    Account.objects.filter(user=user, is_primary=True, is_active=True)
                    .exclude(id=instance.id if instance else None)
                    .first()
                )

                if current_primary:
                    # Demote current primary
                    current_primary.is_primary = False
                    current_primary.save(update_fields=["is_primary", "updated_at"])

                    logger.info(
                        f"Demoted existing primary account: {current_primary.id}"
                    )

                # Ensure new primary account is active
                if "is_active" in validated_data and not validated_data["is_active"]:
                    validated_data["is_active"] = True
                    logger.info("Forced is_active=True for primary account")

                logger.info(f"Setting account as primary")

        except Exception as e:
            logger.error(f"Error handling primary account logic: {e}")
            raise


class AccountListSerializer(BaseAccountSerializer):
    """
    Lightweight serializer for account listing.

    Optimized for:
    - Account dashboards
    - Dropdown selection
    - List views with minimal data
    """

    currency_code = serializers.CharField(source="currency.code", read_only=True)
    currency_symbol = serializers.CharField(source="currency.symbol", read_only=True)
    can_transact = serializers.SerializerMethodField()

    class Meta:
        model = Account
        fields = [
            "id",
            "name",
            "account_type",
            "currency_code",
            "currency_symbol",
            "current_balance",
            "formatted_balance",
            "is_primary",
            "is_active",
            "is_locked",
            "can_transact",
            "created_at",
        ]
        read_only_fields = fields

    def get_can_transact(self, obj: Account) -> bool:
        """Check if account can be used for transactions."""
        return obj.is_active and not obj.is_locked


class AccountDetailSerializer(AccountSerializer):
    """
    Detailed serializer for account retrieval.

    Includes:
    - Complete account information
    - Recent transaction summary
    - Balance history metadata
    - Usage statistics
    """

    recent_transactions = serializers.SerializerMethodField()
    balance_trend = serializers.SerializerMethodField()
    transaction_stats = serializers.SerializerMethodField()

    class Meta(AccountSerializer.Meta):
        fields = AccountSerializer.Meta.fields + [
            "recent_transactions",
            "balance_trend",
            "transaction_stats",
        ]

    def get_recent_transactions(self, obj: Account) -> List[Dict[str, Any]]:
        """Get recent transactions for this account."""
        from .models import Transaction

        try:
            recent_txs = Transaction.objects.filter(
                account=obj, status=choices.TransactionStatus.COMPLETED
            ).order_by("-transaction_date")[:5]

            return [
                {
                    "id": str(tx.id),
                    "name": tx.name,
                    "amount": str(tx.amount),
                    "type": tx.transaction_type,
                    "date": tx.transaction_date,
                    "category": tx.category.name if tx.category else None,
                }
                for tx in recent_txs
            ]
        except Exception as e:
            logger.warning(
                f"Error getting recent transactions for account {obj.id}: {e}"
            )
            return []

    def get_balance_trend(self, obj: Account) -> Dict[str, Any]:
        """Get balance trend information."""
        try:
            # Get balance 30 days ago
            thirty_days_ago = timezone.now().date() - timezone.timedelta(days=30)
            history = obj.get_balance_history(thirty_days_ago, timezone.now().date())

            if len(history) >= 2:
                old_balance = history[0]["balance"]
                new_balance = history[-1]["balance"]
                change = new_balance - old_balance
                percent_change = (
                    (change / abs(old_balance)) * 100 if old_balance != 0 else 0
                )

                return {
                    "old_balance": str(old_balance),
                    "new_balance": str(new_balance),
                    "change": str(change),
                    "percent_change": float(percent_change),
                    "direction": (
                        "up" if change > 0 else "down" if change < 0 else "same"
                    ),
                    "period_days": 30,
                }

            return {
                "old_balance": str(obj.initial_balance),
                "new_balance": str(obj.current_balance),
                "change": str(obj.current_balance - obj.initial_balance),
                "direction": "same",
                "period_days": 0,
            }

        except Exception as e:
            logger.warning(f"Error getting balance trend for account {obj.id}: {e}")
            return {}

    def get_transaction_stats(self, obj: Account) -> Dict[str, Any]:
        """Get transaction statistics for this account."""
        from .models import Transaction
        from django.db.models import Count, Sum

        try:
            stats = Transaction.objects.filter(
                account=obj, status=choices.TransactionStatus.COMPLETED
            ).aggregate(
                total_count=Count("id"),
                total_income=Sum(
                    "amount",
                    filter=models.Q(transaction_type=choices.TransactionType.INCOME),
                ),
                total_expense=Sum(
                    "amount",
                    filter=models.Q(transaction_type=choices.TransactionType.EXPENSE),
                ),
            )

            return {
                "total_transactions": stats["total_count"] or 0,
                "total_income": str(stats["total_income"] or Decimal("0.00")),
                "total_expense": str(stats["total_expense"] or Decimal("0.00")),
                "net_flow": str(
                    (stats["total_income"] or Decimal("0.00"))
                    - (stats["total_expense"] or Decimal("0.00"))
                ),
            }

        except Exception as e:
            logger.warning(f"Error getting transaction stats for account {obj.id}: {e}")
            return {}


class AccountCreateSerializer(AccountSerializer):
    """
    Specialized serializer for account creation only.

    Optimized for:
    - Creation form/API
    - Minimal validation overhead
    - Clear error messages for creation-specific issues
    """

    class Meta(AccountSerializer.Meta):
        # Remove read-only fields that don't apply to creation
        read_only_fields = [
            "id",
            "current_balance",
            "reconciled_balance",
            "reconciled_at",
            "formatted_balance",
            "available_balance",
            "can_be_primary",
            "last_synced_at",
            "balance_updated_at",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        """Additional validation specific to creation."""
        attrs = super().validate(attrs)

        # Ensure initial balance is provided or default to 0
        if "initial_balance" not in attrs:
            attrs["initial_balance"] = Decimal("0.00")

        # Set current_balance equal to initial_balance on creation
        attrs["current_balance"] = attrs["initial_balance"]

        return attrs


class AccountUpdateSerializer(AccountSerializer):
    """
    Specialized serializer for account updates only.

    Optimized for:
    - Update form/API
    - Partial updates
    - Field-specific validation
    """

    class Meta(AccountSerializer.Meta):
        # Fields that cannot be updated after creation
        read_only_fields = AccountSerializer.Meta.read_only_fields + [
            "currency_id",  # Currency cannot be changed after creation
            "account_type",  # Account type cannot be changed after creation
        ]

    def validate_currency_id(self, value):
        """Prevent currency changes after account creation."""
        if self.instance and self.instance.currency != value:
            raise serializers.ValidationError(
                _("Cannot change account currency after creation."),
                code="currency_change_not_allowed",
            )
        return value

    def validate_account_type(self, value):
        """Prevent account type changes after creation."""
        if self.instance and self.instance.account_type != value:
            raise serializers.ValidationError(
                _("Cannot change account type after creation."),
                code="account_type_change_not_allowed",
            )
        return value


class AccountReconcileSerializer(serializers.Serializer):
    """
    Serializer for account reconciliation.

    Used to reconcile account balances with external statements.
    """

    reconciled_balance = serializers.DecimalField(
        max_digits=18,
        decimal_places=2,
        required=True,
        help_text=_("Reconciled balance from external statement"),
    )
    reconciliation_date = serializers.DateField(
        required=False,
        default=timezone.now().date,
        help_text=_("Date of reconciliation (defaults to today)"),
    )
    notes = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=500,
        help_text=_("Notes about the reconciliation"),
    )

    def validate_reconciled_balance(self, value: Decimal) -> Decimal:
        """Validate reconciled balance."""
        return self._validate_balance("reconciled_balance", value)

    def create(self, validated_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Reconcile account balance.

        Args:
            validated_data: Validated reconciliation data

        Returns:
            Reconciliation result
        """
        account = self.context["account"]
        user = self._get_request_user()

        reconciled_balance = validated_data["reconciled_balance"]
        reconciliation_date = validated_data.get("reconciliation_date")
        notes = validated_data.get("notes", "")

        logger.info(
            f"Reconciling account {account.id}: "
            f"current={account.current_balance}, "
            f"reconciled={reconciled_balance}"
        )

        try:
            with db_transaction.atomic():
                # Update account reconciliation fields
                account.reconciled_balance = reconciled_balance
                account.reconciled_at = timezone.now()
                account.save(
                    update_fields=["reconciled_balance", "reconciled_at", "updated_at"]
                )

                # Calculate difference
                difference = reconciled_balance - account.current_balance

                # Create reconciliation record (if you have a Reconciliation model)
                # Reconciliation.objects.create(
                #     account=account,
                #     user=user,
                #     reconciled_balance=reconciled_balance,
                #     actual_balance=account.current_balance,
                #     difference=difference,
                #     notes=notes,
                #     reconciliation_date=reconciliation_date,
                # )

                result = {
                    "account_id": str(account.id),
                    "account_name": account.name,
                    "current_balance": str(account.current_balance),
                    "reconciled_balance": str(reconciled_balance),
                    "difference": str(difference),
                    "reconciled_at": account.reconciled_at.isoformat(),
                    "notes": notes,
                    "requires_adjustment": abs(difference)
                    > Decimal("0.01"),  # Tolerance
                }

                if abs(difference) > Decimal("0.01"):
                    logger.warning(
                        f"Reconciliation difference detected: {difference} "
                        f"for account {account.id}"
                    )
                    result["adjustment_suggestion"] = (
                        f"Consider creating an adjustment transaction of {difference}"
                    )

                logger.info(f"Account {account.id} reconciled successfully")
                return result

        except Exception as e:
            logger.exception(f"Error reconciling account {account.id}: {e}")
            raise serializers.ValidationError(
                _("Failed to reconcile account. Please try again."),
                code="reconciliation_failed",
            )


# Factory function for getting appropriate serializer
def get_account_serializer(action: str = "default") -> serializers.Serializer:
    """
    Get appropriate serializer based on action.

    Args:
        action: Serializer action ('list', 'detail', 'create', 'update', 'reconcile', 'default')

    Returns:
        Appropriate serializer class
    """
    serializer_map = {
        "list": AccountListSerializer,
        "retrieve": AccountDetailSerializer,
        "create": AccountCreateSerializer,
        "update": AccountUpdateSerializer,
        "partial_update": AccountUpdateSerializer,
        "reconcile": AccountReconcileSerializer,
        "default": AccountSerializer,
    }

    return serializer_map.get(action, AccountSerializer)


class TransactionSerializer(serializers.ModelSerializer):
    """
    Serializer for Transaction model with comprehensive validation and security.

    Key Features:
    - Multi-currency validation and conversion
    - Ownership validation for accounts and categories
    - Computed fields for UI convenience
    - Robust error handling and logging
    - Atomic operations for data integrity

    Validation Layers:
    1. Field-level validation (amount, exchange_rate)
    2. Object-level validation (business rules, ownership)
    3. Model-level validation via full_clean()
    """

    # Write fields
    account = serializers.PrimaryKeyRelatedField(
        queryset=Account.objects.all(),
        required=False,
        allow_null=True,
    )
    category = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.all(),
        required=True,
    )

    # Automatically use request.user
    user = serializers.HiddenField(default=serializers.CurrentUserDefault())

    # Computed Read-Only Fields
    currency_converted_amount = serializers.SerializerMethodField(read_only=True)
    is_future_transaction = serializers.SerializerMethodField(read_only=True)
    display_name = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Transaction
        fields = [
            "id",
            "user",
            "account",
            "category",
            "name",
            "transaction_type",
            "amount",
            "original_amount",
            "original_currency",
            "exchange_rate",
            "currency",
            "description",
            "notes",
            "status",
            "tags",
            "attachments",
            "is_recurring",
            "is_transfer",
            "transaction_date",
            # Computed
            "currency_converted_amount",
            "is_future_transaction",
            "display_name",
            # Metadata
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
            "currency_converted_amount",
            "is_future_transaction",
            "display_name",
        ]

    # Computed Field Helpers
    @extend_schema_field(serializers.DecimalField(max_digits=18, decimal_places=2))
    def get_currency_converted_amount(self, obj) -> float:
        """Return the calculated amount based on original_amount * exchange_rate."""
        try:
            if obj.original_amount and obj.exchange_rate:
                return round(obj.original_amount * obj.exchange_rate, 2)
            return obj.amount
        except Exception:
            return None

    @extend_schema_field(serializers.BooleanField())
    def get_is_future_transaction(self, obj) -> bool:
        """True if transaction_date is in the future."""
        return obj.transaction_date > date.today()

    @extend_schema_field(serializers.CharField())
    def get_display_name(self, obj) -> str:
        """
        Useful display label used in UIs.
        Example: 'Groceries - 45 USD (expense)'
        """
        return f"{obj.name} - {obj.amount} {obj.currency} ({obj.transaction_type})"

    # Field-level Validation
    def validate_amount(self, value):
        """Ensure transaction amount is positive."""
        if value <= 0:
            logger.warning("Validation failed: amount <= 0")
            raise serializers.ValidationError("Amount must be a positive value.")
        return value

    def validate_exchange_rate(self, value):
        """Ensure exchange rate is positive."""
        if value <= 0:
            logger.warning("Validation failed: exchange_rate <= 0")
            raise serializers.ValidationError("Exchange rate must be positive.")
        return value

    # Object-level Validation
    def validate(self, data):
        """
        Complex validation:
        - Currency conversion fields must be consistent.
        - Category must be owned by the user if not system category.
        - Account must belong to the user.
        """
        request = self.context["request"]
        user = request.user

        account = data.get("account")
        # On updates (PATCH), category might not be present in `data`.
        # We should get it from the data or from the existing instance.
        category = data.get("category") or getattr(self.instance, "category", None)

        # Validate account belongs to user
        if account and account.user != user and not user.is_staff:
            logger.warning(
                f"User {user.id} attempted to use another user's account {account.id}"
            )
            raise serializers.ValidationError(
                {"account": "You cannot use another user's account."}
            )

        # Validate category belongs to user (unless system category)
        if category and not category.is_system_category and category.user != user:
            logger.warning(
                f"User {user.id} attempted to use another user's category {category.id}"
            )
            raise serializers.ValidationError(
                {"category": "You cannot use another user's category."}
            )

        # Currency conversion validation
        exchange_rate = data.get("exchange_rate", Decimal("1"))
        original_amount = data.get("original_amount")
        original_currency = data.get("original_currency")

        if exchange_rate != 1:
            if not original_amount or not original_currency:
                logger.warning(
                    "Missing original_amount/original_currency for currency conversion"
                )
                raise serializers.ValidationError(
                    {
                        "original_amount": (
                            "original_amount & original_currency are required "
                            "when exchange_rate is not 1."
                        )
                    }
                )

        return data

    def create(self, validated_data):
        """
        Create a new transaction instance.
        """
        try:
            transaction = Transaction.objects.create(**validated_data)
            logger.info(
                f"Transaction created: {transaction.id} by user {transaction.user_id}"
            )
            return transaction
        except Exception as e:
            logger.exception(f"Failed to create transaction: {e}")
            raise serializers.ValidationError(
                {"error": "Failed to create transaction. Please try again."}
            )

    def update(self, instance, validated_data):
        """
        Update an existing transaction instance.
        """
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        try:
            instance.full_clean()  # enforce model-level validations
            instance.save()
            logger.info(
                f"Transaction updated: {instance.id} by user {instance.user_id}"
            )
            return instance
        except Exception as e:
            logger.exception(f"Failed to update transaction: {e}")
            raise serializers.ValidationError(
                {"error": "Failed to update transaction. Please try again."}
            )
