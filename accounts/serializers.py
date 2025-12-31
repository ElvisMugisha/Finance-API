from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models, transaction as db_transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import exceptions, serializers

from core.models import Category, Currency
from core.serializers import (
    CurrencySerializer,
    CategoryListSerializer,
    CurrencyListSerializer,
)
from utils import choices, loggings

from .models import (
    Account,
    Budget,
    BudgetCategory,
    FinancialGoal,
    Report,
    Transaction,
    RecurringTransaction,
)

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
                self._log_validation_warning(f"Extreme {field_name} value: {value}")
                # Log but allow if within database constraints

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
        from core.serializers import CurrencyListSerializer  # Avoid circular import

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
        if value < 0:
            raise serializers.ValidationError(
                _("Initial balance cannot be negative."),
                code="negative_balance",
            )
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
        if is_primary:
            if not is_active:
                raise serializers.ValidationError(
                    {"is_primary": _("Primary account must be active.")}
                )
            # We check instance for is_locked if not in attrs
            is_locked = attrs.get(
                "is_locked", getattr(self.instance, "is_locked", False)
            )
            if is_locked:
                raise serializers.ValidationError(
                    {"is_primary": _("Locked account cannot be primary.")}
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
        from django.db.models import Count, Sum

        from .models import Transaction

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

            # Format with 2 decimal places
            total_income = stats["total_income"] or Decimal("0.00")
            total_expense = stats["total_expense"] or Decimal("0.00")
            net_flow = total_income - total_expense

            return {
                "total_transactions": stats["total_count"] or 0,
                "total_income": f"{total_income:.2f}",
                "total_expense": f"{total_expense:.2f}",
                "net_flow": f"{net_flow:.2f}",
            }

        except Exception as e:
            logger.warning(f"Error getting transaction stats for account {obj.id}: {e}")
            return {
                "total_transactions": 0,
                "total_income": "0.00",
                "total_expense": "0.00",
                "net_flow": "0.00",
            }


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
        # We DO NOT put currency_id and account_type here because we want to
        # raise specific validation errors if the user tries to change them,
        # rather than silently ignoring the input (which read_only does).
        pass  # read_only_fields are inherited from AccountSerializer.Meta and are appropriate for updates.

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

    def _get_request_user(self):
        """Safely get request user from context."""
        request = self.context.get("request")
        return request.user if request and request.user.is_authenticated else None

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
                user = self._get_request_user()
                logger.warning(
                    f"User {user.id if user else 'anon'}: Unusual {field_name} value: {value}"
                )
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
        help_text=_("Account associated with this transaction"),
    )
    category = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.all(),
        required=True,
        help_text=_("Transaction category (must belong to user)"),
    )

    # Hidden field - automatically set from request
    user = serializers.HiddenField(
        default=serializers.CurrentUserDefault(),
        help_text=_("Transaction owner (automatically set)"),
    )

    # === Computed Read-Only Fields ===
    currency = serializers.SerializerMethodField(
        read_only=True,
        help_text=_("Account currency code"),
    )
    currency_converted_amount = serializers.SerializerMethodField(
        read_only=True,
        help_text=_(
            "Amount converted to account currency (original_amount * exchange_rate)"
        ),
    )

    is_future_transaction = serializers.SerializerMethodField(
        read_only=True,
        help_text=_("True if transaction_date is in the future"),
    )

    display_name = serializers.SerializerMethodField(
        read_only=True,
        help_text=_("Formatted display name for UI"),
    )

    transfer_account = serializers.PrimaryKeyRelatedField(
        queryset=Account.objects.all(),
        required=False,
        allow_null=True,
        help_text=_("Account to transfer to (for transfer transactions only)"),
    )

    recurring_transaction = serializers.PrimaryKeyRelatedField(
        queryset=RecurringTransaction.objects.all(),
        required=False,
        allow_null=True,
        help_text=_("Linked recurring transaction"),
    )

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
            "status",
            "tags",
            "attachments",
            "is_recurring",
            "recurring_transaction",
            "is_transfer",
            "transfer_account",
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
    @extend_schema_field(serializers.CharField())
    def get_currency(self, obj: Transaction) -> str:
        """
        Get the currency code for the transaction.
        Priority: Account currency > original currency > default 'USD'.
        """
        if obj.account and obj.account.currency:
            return obj.account.currency.code
        if obj.original_currency:
            return obj.original_currency.code
        return "USD"

    @extend_schema_field(serializers.DecimalField(max_digits=18, decimal_places=2))
    def get_currency_converted_amount(self, obj: Transaction) -> Optional[Decimal]:
        """
        Calculate and return converted amount based on original_amount * exchange_rate.

        Args:
            obj: Transaction instance

        Returns:
            Converted amount or None if calculation fails
        """
        try:
            if obj.original_amount and obj.exchange_rate:
                return round(obj.original_amount * obj.exchange_rate, 2)
            return obj.amount
        except (TypeError, ValueError, AttributeError) as e:
            logger.error(f"Failed to calculate currency converted amount: {e}")
            return None

    @extend_schema_field(serializers.BooleanField())
    def get_is_future_transaction(self, obj: Transaction) -> bool:
        """
        Determine if transaction date is in the future.

        Args:
            obj: Transaction instance

        Returns:
            True if transaction date is in the future
        """
        try:
            return obj.transaction_date > date.today()
        except (AttributeError, TypeError) as e:
            logger.error(f"Failed to determine if transaction is future: {e}")
            return False

    @extend_schema_field(serializers.CharField())
    def get_display_name(self, obj: Transaction) -> str:
        """
        Generate a user-friendly display name for the transaction.

        Format: 'Transaction Name - Amount Currency (Type)'

        Args:
            obj: Transaction instance

        Returns:
            Formatted display string
        """
        try:
            currency_code = self.get_currency(obj)
            return f"{obj.name} - {obj.amount} {currency_code} ({obj.transaction_type})"
        except Exception as e:
            logger.error(f"Failed to generate display name: {e}")
            return str(obj.id)

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
        if not request:
            logger.error("Serializer context missing request object")
            raise serializers.ValidationError(_("Invalid request context."))

        user = request.user
        instance = getattr(self, "instance", None)

        # Validate account ownership
        account = data.get("account", getattr(instance, "account", None))
        if account and account.user != user and not user.is_staff:
            logger.warning(
                f"User {user.id} attempted to use account {account.id} owned by {account.user.id}",
                extra={"user": user.id, "account_user": account.user.id},
            )
            # Use data instead of attrs
            raise serializers.ValidationError(
                {"account": _("You cannot use another user's account.")}
            )

        # Validate category ownership (unless system category)
        category = data.get("category", getattr(instance, "category", None))
        if category:
            if (
                not category.is_system_category
                and category.user != user
                and not user.is_staff
            ):
                logger.warning(
                    f"User {user.id} attempted to use category {category.id} owned by {category.user.id}",
                    extra={"user": user.id, "category_user": category.user.id},
                )
                raise serializers.ValidationError(
                    {"category": _("You cannot use another user's category.")}
                )

            # Validate category type matches transaction type
            transaction_type = data.get(
                "transaction_type", getattr(instance, "transaction_type", None)
            )
            if transaction_type and category.category_type != transaction_type:
                raise serializers.ValidationError(
                    {
                        "category": _(
                            f"Category type '{category.category_type}' does not match "
                            f"transaction type '{transaction_type}'."
                        )
                    }
                )

        # Handle currency conversion automatically
        self._handle_currency_conversion(data, instance)

        # Validate transfer consistency if needed
        if data.get("is_transfer", getattr(instance, "is_transfer", False)):
            self._validate_transfer(data, instance)

        return data

    def _validate_transfer(
        self, data: Dict[str, Any], instance: Optional[Transaction]
    ) -> None:
        """
        Validate transfer-specific business rules.
        """
        transfer_account = data.get(
            "transfer_account", getattr(instance, "transfer_account", None)
        )
        account = data.get("account", getattr(instance, "account", None))

        # Check same account first (most specific error)
        if account and transfer_account and transfer_account.id == account.id:
            raise serializers.ValidationError(
                {"transfer_account": _("Cannot transfer to the same account.")}
            )

        # Then check if transfer_account is required
        if not transfer_account:
            raise serializers.ValidationError(
                {"transfer_account": _("Transfer account is required for transfers.")}
            )

    def _handle_currency_conversion(
        self, data: Dict[str, Any], instance: Optional[Transaction]
    ) -> None:
        """
        Support automatic currency conversion:
        If original_amount and original_currency are provided, calculate
        amount and exchange_rate automatically if they are missing or mismatched.
        """
        account = data.get("account", getattr(instance, "account", None))
        if not account:
            return  # Cannot convert without target account currency

        original_amount = data.get(
            "original_amount", getattr(instance, "original_amount", None)
        )
        original_currency = data.get(
            "original_currency", getattr(instance, "original_currency", None)
        )
        exchange_rate = data.get("exchange_rate")
        amount = data.get("amount")

        # Case 1: Original currency is provided and differs from account currency
        if original_currency and original_currency.id != account.currency.id:
            if original_amount is None:
                raise serializers.ValidationError(
                    {
                        "original_amount": _(
                            "Original amount required for cross-currency."
                        )
                    }
                )

            # Perform automatic conversion if amount or rate is missing
            if amount is None or exchange_rate is None:
                logger.info(
                    f"Auto-converting {original_amount} {original_currency.code} "
                    f"to {account.currency.code}"
                )
                converted_amount = original_currency.convert_amount(
                    original_amount, account.currency
                )

                if converted_amount is not None:
                    if amount is None:
                        data["amount"] = converted_amount
                    if exchange_rate is None:
                        # Implied rate: Target / Original
                        data["exchange_rate"] = (
                            converted_amount / original_amount
                        ).quantize(Decimal("0.0000000001"), rounding="ROUND_HALF_UP")
                else:
                    raise serializers.ValidationError(
                        {
                            "original_currency": _(
                                "Exchange rate not available for conversion."
                            )
                        }
                    )

        # Case 2: No original currency, or same as account
        elif original_currency and original_currency.id == account.currency.id:
            # Sync original to amount if original is provided for clarity
            if original_amount is not None and amount is None:
                data["amount"] = original_amount
            data["exchange_rate"] = Decimal("1.0")

        # Case 3: Only amount is provided, original values should match it
        elif amount is not None and original_amount is None:
            data["original_amount"] = amount
            data["original_currency"] = account.currency
            data["exchange_rate"] = Decimal("1.0")

    def create(self, validated_data: Dict[str, Any]) -> Transaction:
        """
        Create a new transaction with atomic operation and proper error handling.

        Args:
            validated_data: Validated transaction data

        Returns:
            Created transaction instance

        Raises:
            serializers.ValidationError: If creation fails
        """
        try:
            with db_transaction.atomic():
                transaction = Transaction.objects.create(**validated_data)

                # If transaction is completed, update account balance
                # REMOVED: Let the model handle this in its save() method
                # if transaction.status == choices.TransactionStatus.COMPLETED:
                #     self._update_account_balance(transaction)

                logger.info(
                    f"Transaction created: {transaction.id}",
                    extra={
                        "user_id": transaction.user_id,
                        "account_id": transaction.account_id,
                        "amount": transaction.amount,
                    },
                )
                return transaction

        except Exception as e:
            logger.exception(f"Failed to create transaction: {e}")
            raise serializers.ValidationError(
                {"error": _("Failed to create transaction. Please try again.")}
            )

    def update(
        self, instance: Transaction, validated_data: Dict[str, Any]
    ) -> Transaction:
        """
        Update an existing transaction with atomic operation.
        """
        try:
            with db_transaction.atomic():
                # Track status change for balance update
                old_status = instance.status
                old_amount = instance.amount

                # Update fields
                for attr, value in validated_data.items():
                    setattr(instance, attr, value)

                # Run model validation
                instance.full_clean()

                # Save the instance
                instance.save()

                logger.info(
                    f"Transaction updated: {instance.id}",
                    extra={
                        "user_id": instance.user_id,
                        "account_id": instance.account_id,
                        "changes": list(validated_data.keys()),
                    },
                )
                return instance

        except Exception as e:
            logger.exception(f"Failed to update transaction {instance.id}: {e}")
            raise serializers.ValidationError(
                {"error": _("Failed to update transaction. Please try again.")}
            )

    def to_representation(self, instance: Transaction) -> Dict[str, Any]:
        """
        Custom representation to include additional computed fields.
        """
        representation = super().to_representation(instance)

        # Add extra computed properties
        try:
            representation["is_verified"] = instance.is_verified
            representation["converted_amount"] = instance.converted_amount
        except AttributeError as e:
            logger.warning(f"Could not add computed properties: {e}")

        return representation


class TransactionCreateSerializer(TransactionSerializer):
    """Serializer for creating transactions with strict validation."""

    class Meta(TransactionSerializer.Meta):
        fields = [
            "account",
            "category",
            "name",
            "transaction_type",
            "amount",
            "original_amount",
            "original_currency",
            "exchange_rate",
            "description",
            "status",
            "transaction_date",
            "is_transfer",
            "transfer_account",
        ]
        extra_kwargs = {
            "transaction_type": {"required": True},
            "amount": {"required": True},
            "transaction_date": {"required": True},
        }


class TransactionUpdateSerializer(TransactionSerializer):
    """Serializer for updating transactions with business rules."""

    class Meta(TransactionSerializer.Meta):
        fields = [
            "name",
            "amount",
            "description",
            "status",
            "transaction_date",
            "category",
            "account",
            "tags",
            "attachments",
        ]
        read_only_fields = ["transaction_type", "is_transfer"]


class TransactionVerificationSerializer(serializers.ModelSerializer):
    """Serializer for verifying transactions."""

    class Meta:
        model = Transaction
        fields = ["status", "posted_date"]
        read_only_fields = ["status"]

    def update(self, instance, validated_data):
        """Mark transaction as verified."""
        instance.status = choices.TransactionStatus.COMPLETED
        instance.posted_date = validated_data.get("posted_date")
        instance.save()

        # Account balance is updated automatically by Transaction.save() logic
        # when status changes to COMPLETED.

        return instance


class BaseBudgetSerializer(serializers.ModelSerializer):
    """Base serializer with common budget functionality."""

    def _get_request_user(self):
        """Safely get request user from context."""
        request = self.context.get("request")
        return request.user if request and request.user.is_authenticated else None

    def _log_validation_warning(self, message: str, **kwargs) -> None:
        """Log validation warnings consistently."""
        user = self._get_request_user()
        user_id = user.id if user else "anonymous"
        logger.warning(f"User {user_id}: {message}", **kwargs)

    def _validate_amount(self, field_name: str, value: Decimal) -> Decimal:
        """Common amount validation logic."""
        try:
            # Ensure it's a valid Decimal
            if not isinstance(value, Decimal):
                value = Decimal(str(value))

            # Check reasonable bounds
            min_limit = Decimal("0.01")  # Minimum budget
            max_limit = Decimal("1000000000")  # 1 billion max

            if value < min_limit:
                raise serializers.ValidationError(
                    _(f"{field_name.replace('_', ' ').title()} must be at least 0.01."),
                    code=f"invalid_{field_name}_min",
                )

            if value > max_limit:
                self._log_validation_warning(f"Extreme {field_name} value: {value}")

            return value

        except (InvalidOperation, TypeError, ValueError) as e:
            logger.error(f"Invalid {field_name} value: {value}, error: {e}")
            raise serializers.ValidationError(
                _(
                    f"{field_name.replace('_', ' ').title()} must be a valid decimal number."
                ),
                code=f"invalid_{field_name}",
            )


class BudgetCategorySerializer(serializers.ModelSerializer):
    """
    Serializer for BudgetCategory model.

    Handles category allocations within budgets.
    Provides calculated fields for spending tracking.
    """

    # Nested category details (read-only)
    category_name = serializers.CharField(source="category.name", read_only=True)
    category_type = serializers.CharField(
        source="category.category_type", read_only=True
    )

    # Calculated fields
    utilization_percentage = serializers.SerializerMethodField()
    is_over_budget = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    daily_allowance = serializers.SerializerMethodField()

    class Meta:
        model = BudgetCategory
        fields = [
            "id",
            "budget",
            "category",
            "category_name",
            "category_type",
            "allocated_amount",
            "spent_amount",
            "remaining_amount",
            "percentage_used",
            "utilization_percentage",
            "is_over_budget",
            "status",
            "daily_allowance",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "spent_amount",
            "remaining_amount",
            "percentage_used",
            "utilization_percentage",
            "is_over_budget",
            "status",
            "daily_allowance",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            "allocated_amount": {
                "help_text": _("Amount allocated to this category"),
                "min_value": Decimal("0.01"),
            },
            "category": {
                "help_text": _("Category for this allocation"),
            },
        }

    def get_utilization_percentage(self, obj: BudgetCategory) -> float:
        """Calculate utilization percentage."""
        try:
            if obj.allocated_amount > 0:
                return float((obj.spent_amount / obj.allocated_amount) * 100)
            return 0.0
        except Exception as e:
            logger.error(
                f"Error calculating utilization for budget category {obj.id}: {e}"
            )
            return 0.0

    def get_is_over_budget(self, obj: BudgetCategory) -> bool:
        """Check if category allocation is exceeded."""
        return obj.spent_amount > obj.allocated_amount

    def get_status(self, obj: BudgetCategory) -> str:
        """Get human-readable status of the category allocation."""
        utilization = self.get_utilization_percentage(obj)
        if utilization >= 100:
            return "exceeded"
        if utilization >= 90:
            return "critical"
        if utilization >= 75:
            return "near_limit"
        return "on_track"

    def get_daily_allowance(self, obj: BudgetCategory) -> Optional[str]:
        """Calculate how much can be spent per day for the rest of the period."""
        try:
            days_remaining = obj.budget.days_remaining
            if days_remaining <= 0:
                return "0.00"

            allowance = obj.remaining_amount / Decimal(str(days_remaining))
            return f"{max(allowance, Decimal('0.00')):.2f}"
        except Exception as e:
            logger.error(
                f"Error calculating daily allowance for budget category {obj.id}: {e}"
            )
            return None

    def validate_allocated_amount(self, value: Decimal) -> Decimal:
        """Validate allocated amount."""
        if value <= 0:
            raise serializers.ValidationError(
                _("Allocated amount must be greater than zero."),
                code="invalid_allocated_amount",
            )
        return value

    def validate(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
        """Cross-field validation."""
        logger.debug("Validating budget category")

        request = self.context.get("request")
        user = request.user if request else None

        # Validate budget ownership
        budget = attrs.get("budget")
        if (
            budget
            and user
            and budget.user != user
            and not (user.is_staff or user.is_superuser)
        ):
            raise serializers.ValidationError(
                {"budget": _("You cannot manage categories for another user's budget.")}
            )

        # Validate category ownership (unless system category)
        category = attrs.get("category")
        if category:
            if (
                not category.is_system_category
                and user
                and category.user != user
                and not (user.is_staff or user.is_superuser)
            ):
                raise serializers.ValidationError(
                    {"category": _("You cannot use another user's category.")}
                )

            # Validate category type (must be expense)
            if category.category_type != choices.TransactionType.EXPENSE:
                raise serializers.ValidationError(
                    {"category": _("Budget categories must be expense categories.")}
                )

        return attrs


class BudgetSerializer(BaseBudgetSerializer):
    """
    Primary serializer for Budget CRUD operations.

    Responsibilities:
    - Handles creation and updates of budgets
    - Validates budget type-specific rules
    - Manages currency and category relationships
    - Calculates spending and remaining amounts
    - Supports budget categories for multi-category budgets

    Security:
    - Users can only manage their own budgets
    - Currency and category must be active
    - Amount validations
    """

    # Currency handling: write with ID, read with full details
    currency_id = serializers.PrimaryKeyRelatedField(
        queryset=Currency.objects.filter(is_active=True),
        source="currency",
        write_only=True,
        help_text=_("ID of the currency for this budget"),
    )
    currency = serializers.SerializerMethodField(read_only=True)

    # Category handling (for category budgets)
    category_id = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.filter(
            is_active=True, category_type=choices.TransactionType.EXPENSE
        ),
        source="category",
        write_only=True,
        required=False,
        allow_null=True,
        help_text=_("ID of the category for category budgets"),
    )
    category_details = serializers.SerializerMethodField(read_only=True)

    # Budget categories (for multi-category budgets)
    budget_categories = BudgetCategorySerializer(many=True, read_only=True)

    # Calculated fields
    utilization_percentage = serializers.SerializerMethodField()
    available_amount = serializers.SerializerMethodField()
    is_over_budget_flag = serializers.SerializerMethodField()
    days_left = serializers.SerializerMethodField()
    should_alert = serializers.SerializerMethodField()

    class Meta:
        model = Budget
        fields = [
            "id",
            "name",
            "budget_type",
            "category_id",
            "category_details",
            "total_budget",
            "currency_id",
            "currency",
            "total_spent",
            "total_remaining",
            "period_type",
            "start_date",
            "end_date",
            "rollover_unused",
            "rollover_amount",
            "is_active",
            "description",
            "notification_threshold",
            "last_recalculated_at",
            "budget_categories",
            "utilization_percentage",
            "available_amount",
            "is_over_budget_flag",
            "days_left",
            "should_alert",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "total_spent",
            "total_remaining",
            "last_recalculated_at",
            "budget_categories",
            "utilization_percentage",
            "available_amount",
            "is_over_budget_flag",
            "days_left",
            "should_alert",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            "name": {
                "help_text": _('Budget name (e.g., "Monthly Groceries")'),
                "max_length": 255,
                "trim_whitespace": True,
            },
            "total_budget": {
                "help_text": _("Total budgeted amount"),
                "min_value": Decimal("0.01"),
            },
            "notification_threshold": {
                "help_text": _("Percentage threshold for notifications (0-100)"),
                "min_value": Decimal("0"),
                "max_value": Decimal("100"),
            },
            "rollover_amount": {
                "help_text": _("Amount rolled over from previous period"),
                "min_value": Decimal("0"),
            },
        }

    def get_currency(self, obj: Budget) -> Dict[str, Any]:
        """Get currency details."""

        return CurrencyListSerializer(obj.currency).data

    def get_category_details(self, obj: Budget) -> Optional[Dict[str, Any]]:
        """Get category details for category budgets."""
        if obj.category:
            return CategoryListSerializer(obj.category).data
        return None

    def get_utilization_percentage(self, obj: Budget) -> float:
        """Get budget utilization percentage."""
        return obj.get_utilization_percentage()

    def get_available_amount(self, obj: Budget) -> str:
        """Get available budget amount (0 if over budget)."""
        available = obj.total_remaining
        if available < 0:
            return "0.00"
        return str(available)

    def get_is_over_budget_flag(self, obj: Budget) -> bool:
        """Check if budget is exceeded."""
        return obj.is_over_budget

    def get_days_left(self, obj: Budget) -> int:
        """Get days remaining in budget period."""
        return obj.days_remaining

    def get_should_alert(self, obj: Budget) -> bool:
        """Check if notification should be sent."""
        return obj.should_notify()

    def validate_name(self, value: str) -> str:
        """Validate budget name."""
        value = value.strip()

        if not value:
            raise serializers.ValidationError(
                _("Budget name cannot be empty."), code="empty_name"
            )

        if len(value) > 255:
            raise serializers.ValidationError(
                _("Budget name cannot exceed 255 characters."), code="name_too_long"
            )

        # Check for uniqueness per user (for active budgets)
        user = self._get_request_user()
        if user and self.instance is None:  # Only on creation
            if Budget.objects.filter(user=user, name=value, is_active=True).exists():
                raise serializers.ValidationError(
                    _("An active budget with this name already exists."),
                    code="duplicate_name",
                )

        logger.debug(f"Budget name validation passed: {value}")
        return value

    def validate_total_budget(self, value: Decimal) -> Decimal:
        """Validate total budget amount."""
        return self._validate_amount("total_budget", value)

    def validate_notification_threshold(self, value: Decimal) -> Decimal:
        """Validate notification threshold."""
        if not (Decimal("0") <= value <= Decimal("100")):
            raise serializers.ValidationError(
                _("Notification threshold must be between 0 and 100."),
                code="invalid_threshold",
            )
        return value

    def validate_rollover_amount(self, value: Decimal) -> Decimal:
        """Validate rollover amount."""
        if value < 0:
            raise serializers.ValidationError(
                _("Rollover amount cannot be negative."),
                code="negative_rollover",
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
        logger.debug("Performing cross-field validation for budget")

        # Get values with fallbacks
        budget_type = attrs.get(
            "budget_type", getattr(self.instance, "budget_type", None)
        )
        category = attrs.get("category", getattr(self.instance, "category", None))
        start_date = attrs.get("start_date", getattr(self.instance, "start_date", None))
        end_date = attrs.get("end_date", getattr(self.instance, "end_date", None))

        # Validate dates
        if start_date and end_date:
            if end_date <= start_date:
                raise serializers.ValidationError(
                    {"end_date": _("End date must be after start date.")}
                )

        # Validate category for category budgets
        if budget_type == choices.BudgetType.CATEGORY:
            if not category:
                raise serializers.ValidationError(
                    {"category": _("Category is required for category budgets.")}
                )
            # Ensure category is expense type
            if category.category_type != choices.TransactionType.EXPENSE:
                raise serializers.ValidationError(
                    {"category": _("Budget category must be an expense category.")}
                )

        # Validate no category for overall budgets
        if budget_type == choices.BudgetType.OVERALL and category:
            logger.warning("Overall budget should not have a specific category")
            attrs["category"] = None

        logger.debug("Cross-field validation passed")
        return attrs

    def create(self, validated_data: Dict[str, Any]) -> Budget:
        """
        Create a new budget with business logic.

        Args:
            validated_data: Validated data for budget creation

        Returns:
            Created Budget instance

        Raises:
            serializers.ValidationError: If creation fails
        """
        logger.info("Creating new budget")

        try:
            user = self._get_request_user()

            if not user:
                raise serializers.ValidationError(
                    _("User must be authenticated to create a budget."),
                    code="unauthenticated",
                )

            # Set user
            validated_data["user"] = user

            # Create budget within transaction
            with db_transaction.atomic():
                budget = Budget.objects.create(**validated_data)

            logger.info(
                f"Budget created: id={budget.id}, "
                f"name='{budget.name}', "
                f"user={user.id}, "
                f"type={budget.budget_type}, "
                f"amount={budget.total_budget}"
            )

            return budget

        except DjangoValidationError as e:
            logger.error(f"Model validation failed creating budget: {e}")
            raise serializers.ValidationError(e.message_dict)

        except Exception as e:
            logger.exception(f"Unexpected error creating budget: {e}")
            raise serializers.ValidationError(
                _("An unexpected error occurred while creating the budget."),
                code="creation_error",
            )

    def update(self, instance: Budget, validated_data: Dict[str, Any]) -> Budget:
        """
        Update an existing budget.

        Args:
            instance: Existing Budget instance
            validated_data: Validated data for update

        Returns:
            Updated Budget instance

        Raises:
            serializers.ValidationError: If update fails
        """
        logger.info(f"Updating budget: id={instance.id}, name='{instance.name}'")

        try:
            user = self._get_request_user()

            # Check if user owns this budget
            if instance.user != user:
                self._log_validation_warning(
                    f"User {user.id} attempted to update another user's budget {instance.id}"
                )
                raise serializers.ValidationError(
                    _("Cannot update another user's budget."), code="unauthorized"
                )

            # Update fields
            for attr, value in validated_data.items():
                setattr(instance, attr, value)

            # Save with validation
            instance.save()

            logger.info(
                f"Budget updated: id={instance.id}, "
                f"updated fields={list(validated_data.keys())}"
            )

            return instance

        except DjangoValidationError as e:
            logger.error(f"Model validation failed updating budget {instance.id}: {e}")
            raise serializers.ValidationError(e.message_dict)

        except Exception as e:
            logger.exception(f"Unexpected error updating budget {instance.id}: {e}")
            raise serializers.ValidationError(
                _("An unexpected error occurred while updating the budget."),
                code="update_error",
            )


class BudgetListSerializer(BaseBudgetSerializer):
    """
    Lightweight serializer for budget listing.

    Optimized for:
    - Budget dashboards
    - List views with minimal data
    - Quick overview of budgets
    """

    currency_code = serializers.CharField(source="currency.code", read_only=True)
    currency_symbol = serializers.CharField(source="currency.symbol", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)
    utilization_percentage = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()

    class Meta:
        model = Budget
        fields = [
            "id",
            "name",
            "budget_type",
            "category_name",
            "total_budget",
            "total_spent",
            "total_remaining",
            "currency_code",
            "currency_symbol",
            "period_type",
            "start_date",
            "end_date",
            "is_active",
            "utilization_percentage",
            "status",
            "created_at",
        ]
        read_only_fields = fields

    def get_utilization_percentage(self, obj: Budget) -> float:
        """Get budget utilization percentage."""
        return obj.get_utilization_percentage()

    def get_status(self, obj: Budget) -> str:
        """Get budget status."""
        if not obj.is_active:
            return "inactive"
        if obj.is_over_budget:
            return "exceeded"
        if obj.should_notify():
            return "warning"
        return "on_track"


class BudgetDetailSerializer(BudgetSerializer):
    """
    Detailed serializer for budget retrieval.

    Includes:
    - Complete budget information
    - Budget categories with allocations
    - Spending breakdown
    - Progress indicators
    - Historical data
    """

    spending_by_category = serializers.SerializerMethodField()
    daily_average_spending = serializers.SerializerMethodField()
    projected_spending = serializers.SerializerMethodField()

    class Meta(BudgetSerializer.Meta):
        fields = BudgetSerializer.Meta.fields + [
            "spending_by_category",
            "daily_average_spending",
            "projected_spending",
        ]

    def get_spending_by_category(self, obj: Budget) -> List[Dict[str, Any]]:
        """
        Get spending breakdown by category for this budget.

        For CATEGORY budgets: Only shows spending for that specific category
        For OVERALL budgets: Shows all expense categories
        """
        from django.db.models import Sum

        try:
            # Base query for transactions within budget period
            query = models.Q(
                user=obj.user,
                transaction_type=choices.TransactionType.EXPENSE,
                transaction_date__gte=obj.start_date,
                transaction_date__lte=obj.end_date,
                status=choices.TransactionStatus.COMPLETED,
            )

            # CRITICAL FIX: Filter by budget category if it exists (regardless of budget type)
            if obj.category:
                query &= models.Q(category=obj.category)

            spending = (
                Transaction.objects.filter(query)
                .values("category__name")
                .annotate(total=Sum("amount"))
                .order_by("-total")[:10]
            )

            return [
                {
                    "category": item["category__name"],
                    "amount": str(item["total"]),
                }
                for item in spending
            ]

        except Exception as e:
            logger.warning(f"Error getting spending breakdown for budget {obj.id}: {e}")
            return []

    def get_daily_average_spending(self, obj: Budget) -> str:
        """Calculate daily average spending."""
        try:
            days_elapsed = (timezone.now().date() - obj.start_date).days + 1
            if days_elapsed > 0:
                daily_avg = obj.total_spent / Decimal(str(days_elapsed))
                return str(daily_avg.quantize(Decimal("0.01")))
            return "0.00"
        except Exception as e:
            logger.warning(f"Error calculating daily average for budget {obj.id}: {e}")
            return "0.00"

    def get_projected_spending(self, obj: Budget) -> Dict[str, Any]:
        """Calculate projected spending at current rate."""
        try:
            days_elapsed = (timezone.now().date() - obj.start_date).days + 1
            total_days = (obj.end_date - obj.start_date).days + 1

            if days_elapsed > 0 and total_days > 0:
                daily_avg = obj.total_spent / Decimal(str(days_elapsed))
                projected = daily_avg * Decimal(str(total_days))

                return {
                    "projected_total": str(projected.quantize(Decimal("0.01"))),
                    "projected_vs_budget": str(
                        (projected - obj.total_budget).quantize(Decimal("0.01"))
                    ),
                    "will_exceed": projected > obj.total_budget,
                }

            return {
                "projected_total": "0.00",
                "projected_vs_budget": "0.00",
                "will_exceed": False,
            }

        except Exception as e:
            logger.warning(f"Error calculating projection for budget {obj.id}: {e}")
            return {
                "projected_total": "0.00",
                "projected_vs_budget": "0.00",
                "will_exceed": False,
            }


class BudgetCreateSerializer(BudgetSerializer):
    """
    Specialized serializer for budget creation only.

    Optimized for:
    - Creation form/API
    - Minimal validation overhead
    - Clear error messages for creation-specific issues
    """

    class Meta(BudgetSerializer.Meta):
        # Same fields as BudgetSerializer
        pass

    def validate(self, attrs):
        """Additional validation specific to creation."""
        attrs = super().validate(attrs)

        # Ensure rollover_amount defaults to 0 on creation
        if "rollover_amount" not in attrs:
            attrs["rollover_amount"] = Decimal("0.00")

        # Ensure notification_threshold has a default
        if "notification_threshold" not in attrs:
            attrs["notification_threshold"] = Decimal("80.00")

        return attrs


class BudgetUpdateSerializer(BudgetSerializer):
    """
    Specialized serializer for budget updates only.

    Optimized for:
    - Update form/API
    - Partial updates
    - Field-specific validation
    """

    class Meta(BudgetSerializer.Meta):
        # Same fields as BudgetSerializer
        pass

    def validate_budget_type(self, value):
        """Prevent budget type changes after creation."""
        if self.instance and self.instance.budget_type != value:
            raise serializers.ValidationError(
                _("Cannot change budget type after creation."),
                code="budget_type_change_not_allowed",
            )
        return value

    def validate_currency_id(self, value):
        """Prevent currency changes after creation."""
        if self.instance and self.instance.currency != value:
            raise serializers.ValidationError(
                _("Cannot change budget currency after creation."),
                code="currency_change_not_allowed",
            )
        return value


class BudgetRecalculateSerializer(serializers.Serializer):
    """
    Serializer for triggering budget recalculation.

    Used to manually recalculate spending and update budget status.
    """

    force = serializers.BooleanField(
        default=False,
        help_text=_("Force recalculation even if recently calculated"),
    )

    def create(self, validated_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Recalculate budget spending.

        Args:
            validated_data: Validated recalculation data

        Returns:
            Recalculation result
        """
        budget = self.context["budget"]
        force = validated_data.get("force", False)

        logger.info(f"Recalculating budget {budget.id}, force={force}")

        try:
            # Calculate spending
            spending = budget.calculate_spending()

            return {
                "budget_id": str(budget.id),
                "total_budget": str(budget.total_budget),
                "total_spent": str(budget.total_spent),
                "total_remaining": str(budget.total_remaining),
                "utilization_percentage": budget.get_utilization_percentage(),
                "is_over_budget": budget.is_over_budget,
                "last_recalculated_at": budget.last_recalculated_at,
            }

        except Exception as e:
            logger.exception(f"Error recalculating budget {budget.id}: {e}")
            raise serializers.ValidationError(
                _("An error occurred while recalculating the budget."),
                code="recalculation_error",
            )


class BaseFinancialGoalSerializer(serializers.ModelSerializer):
    """Base serializer with common financial goal functionality."""

    def _get_request_user(self):
        """Safely get request user from context."""
        request = self.context.get("request")
        return request.user if request and request.user.is_authenticated else None

    def _log_validation_warning(self, message: str, **kwargs) -> None:
        """Log validation warnings consistently."""
        user = self._get_request_user()
        user_id = user.id if user else "anonymous"
        logger.warning(f"User {user_id}: {message}", **kwargs)


class FinancialGoalSerializer(BaseFinancialGoalSerializer):
    """
    Primary serializer for Financial Goal CRUD operations.

    Responsibilities:
    - Handles creation and updates of financial goals
    - Validates amounts and dates
    - Manages currency and account relationships
    - Tracks progress
    """

    currency_id = serializers.PrimaryKeyRelatedField(
        queryset=Currency.objects.filter(is_active=True),
        source="currency",
        write_only=True,
        help_text=_("ID of the currency for this goal"),
    )
    currency = serializers.SerializerMethodField(read_only=True)

    linked_account_id = serializers.PrimaryKeyRelatedField(
        queryset=Account.objects.all(),
        source="linked_account",
        write_only=True,
        required=False,
        allow_null=True,
        help_text=_("ID of the linked account for automatic contributions"),
    )
    linked_account_details = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = FinancialGoal
        fields = [
            "id",
            "name",
            "description",
            "goal_type",
            "target_amount",
            "current_amount",
            "currency_id",
            "currency",
            "monthly_contribution",
            "start_date",
            "target_date",
            "achieved_date",
            "priority",
            "is_achieved",
            "is_active",
            "progress_percentage",
            "months_remaining",
            "linked_account_id",
            "linked_account_details",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "current_amount",  # Updated via contributions
            "progress_percentage",
            "months_remaining",
            "is_achieved",
            "achieved_date",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            "name": {
                "help_text": _("Name of the financial goal"),
                "max_length": 255,
                "trim_whitespace": True,
            },
            "target_amount": {
                "help_text": _("Target amount to achieve"),
                "min_value": Decimal("0.01"),
            },
        }

    def get_currency(self, obj: FinancialGoal) -> Dict[str, Any]:
        """Get currency details."""
        return CurrencyListSerializer(obj.currency).data

    def get_linked_account_details(
        self, obj: FinancialGoal
    ) -> Optional[Dict[str, Any]]:
        """Get linked account details."""
        if obj.linked_account:
            return AccountListSerializer(obj.linked_account).data
        return None

    def validate_name(self, value: str) -> str:
        """Validate goal name."""
        value = value.strip()
        if not value:
            raise serializers.ValidationError(_("Goal name cannot be empty."))

        user = self._get_request_user()
        if user and self.instance is None:
            if FinancialGoal.objects.filter(user=user, name=value).exists():
                raise serializers.ValidationError(
                    _("A financial goal with this name already exists.")
                )

        return value

    def validate(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
        """Cross-field validation."""
        start_date = attrs.get(
            "start_date", getattr(self.instance, "start_date", timezone.now().date())
        )
        target_date = attrs.get(
            "target_date", getattr(self.instance, "target_date", None)
        )

        if target_date and start_date and target_date <= start_date:
            raise serializers.ValidationError(
                {"target_date": _("Target date must be after start date.")}
            )

        # Validate linked account ownership
        linked_account = attrs.get("linked_account")
        user = self._get_request_user()
        if linked_account and user and linked_account.user != user:
            raise serializers.ValidationError(
                {"linked_account": _("Linked account must belong to you.")}
            )

        return attrs

    def create(self, validated_data: Dict[str, Any]) -> FinancialGoal:
        """Create new financial goal."""
        try:
            user = self._get_request_user()
            if not user:
                raise serializers.ValidationError(_("User must be authenticated."))

            validated_data["user"] = user

            with db_transaction.atomic():
                goal = FinancialGoal.objects.create(**validated_data)

            logger.info(f"Financial goal created: {goal.id} for user {user.id}")
            return goal
        except Exception as e:
            logger.error(f"Error creating financial goal: {e}")
            raise serializers.ValidationError(_("Failed to create financial goal."))

    def update(
        self, instance: FinancialGoal, validated_data: Dict[str, Any]
    ) -> FinancialGoal:
        """Update financial goal."""
        try:
            user = self._get_request_user()
            if instance.user != user:
                raise serializers.ValidationError(
                    _("Cannot update another user's goal.")
                )

            for attr, value in validated_data.items():
                setattr(instance, attr, value)

            instance.save()
            logger.info(f"Financial goal updated: {instance.id}")
            return instance
        except Exception as e:
            logger.error(f"Error updating financial goal: {e}")
            raise serializers.ValidationError(_("Failed to update financial goal."))


class FinancialGoalListSerializer(BaseFinancialGoalSerializer):
    """Lightweight serializer for listing goals."""

    currency_code = serializers.CharField(source="currency.code", read_only=True)

    class Meta:
        model = FinancialGoal
        fields = [
            "id",
            "name",
            "goal_type",
            "target_amount",
            "current_amount",
            "currency_code",
            "progress_percentage",
            "target_date",
            "months_remaining",
            "is_achieved",
            "priority",
        ]
        read_only_fields = fields


class FinancialGoalDetailSerializer(FinancialGoalSerializer):
    """Detailed serializer with progress summary."""

    progress_summary = serializers.SerializerMethodField()

    class Meta(FinancialGoalSerializer.Meta):
        fields = FinancialGoalSerializer.Meta.fields + ["progress_summary"]

    def get_progress_summary(self, obj: FinancialGoal) -> Dict[str, Any]:
        """Get detailed progress summary."""
        return obj.get_progress_summary()


class FinancialGoalCreateSerializer(FinancialGoalSerializer):
    """Serializer for creation."""

    pass


class FinancialGoalUpdateSerializer(FinancialGoalSerializer):
    """Serializer for updates."""

    class Meta(FinancialGoalSerializer.Meta):
        read_only_fields = FinancialGoalSerializer.Meta.read_only_fields + ["currency"]

    def validate_currency_id(self, value):
        if self.instance and self.instance.currency != value:
            raise serializers.ValidationError(
                _("Cannot change goal currency after creation.")
            )
        return value


class FinancialGoalContributionSerializer(serializers.Serializer):
    """Serializer for adding contributions to a goal."""

    amount = serializers.DecimalField(
        max_digits=18,
        decimal_places=2,
        min_value=Decimal("0.01"),
        help_text=_("Amount to contribute"),
    )
    date = serializers.DateField(
        required=False, default=timezone.now().date, help_text=_("Date of contribution")
    )
    notes = serializers.CharField(
        required=False, allow_blank=True, help_text=_("Optional notes")
    )

    def create(self, validated_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process contribution."""
        goal = self.context["goal"]
        amount = validated_data["amount"]
        contribution_date = validated_data.get("date")

        success = goal.add_contribution(amount, contribution_date)

        if not success:
            raise serializers.ValidationError(_("Failed to add contribution."))

        # Refresh objects to get updated balances
        goal.refresh_from_db()
        if goal.linked_account:
            goal.linked_account.refresh_from_db()

        exceeded_amount = Decimal("0.00")
        if goal.current_amount > goal.target_amount:
            exceeded_amount = goal.current_amount - goal.target_amount

        return {
            "goal_id": str(goal.id),
            "goal_name": goal.name,
            "target_amount": str(goal.target_amount),
            "current_amount": str(goal.current_amount),
            "progress_percentage": float(goal.progress_percentage),
            "is_achieved": goal.is_achieved,
            "is_exceeded": goal.current_amount > goal.target_amount,
            "exceeded_amount": str(exceeded_amount),
            "currency": goal.currency.code,
            "linked_account_balance": (
                str(goal.linked_account.current_balance)
                if goal.linked_account
                else None
            ),
            "message": (
                _("Goal exceeded!")
                if goal.current_amount > goal.target_amount
                else (
                    _("Goal achieved!")
                    if goal.is_achieved
                    else _("Contribution added successfully.")
                )
            ),
        }


def get_financial_goal_serializer(action: str):
    """
    Returns the appropriate FinancialGoal serializer based on the view action.

    Args:
        action (str): The viewset action name.

    Returns:
        Serializer: The serializer class to use.
    """
    serializers_map = {
        "list": FinancialGoalListSerializer,
        "retrieve": FinancialGoalDetailSerializer,
        "create": FinancialGoalCreateSerializer,
        "update": FinancialGoalUpdateSerializer,
        "partial_update": FinancialGoalUpdateSerializer,
        "contribute": FinancialGoalContributionSerializer,
    }
    return serializers_map.get(action, FinancialGoalSerializer)


class ReportSerializer(serializers.ModelSerializer):
    """
    Base serializer for the Report model.

    Provides common fields and read-only status handling for financial reports.
    """

    status_display = serializers.CharField(source="get_status_display", read_only=True)
    report_type_display = serializers.CharField(
        source="get_report_type_display", read_only=True
    )
    format_display = serializers.CharField(source="get_format_display", read_only=True)

    class Meta:
        model = Report
        fields = [
            "id",
            "report_name",
            "report_type",
            "report_type_display",
            "period_start",
            "period_end",
            "format",
            "format_display",
            "status",
            "status_display",
            "created_at",
            "generated_at",
            "expires_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "created_at",
            "generated_at",
            "expires_at",
            "status_display",
            "report_type_display",
            "format_display",
        ]


class ReportListSerializer(ReportSerializer):
    """
    Optimized serializer for listing reports.

    Excludes heavy data fields to ensure performance when listing many reports.
    """

    class Meta(ReportSerializer.Meta):
        pass


class ReportDetailSerializer(ReportSerializer):
    """
    Comprehensive serializer for detailed report view.

    Includes the actual generated data, metadata, and download links.
    """

    download_url = serializers.SerializerMethodField()

    class Meta(ReportSerializer.Meta):
        fields = ReportSerializer.Meta.fields + [
            "data",
            "parameters",
            "error_message",
            "generation_duration",
            "file_size",
            "checksum",
            "download_url",
        ]

    @extend_schema_field(serializers.URLField())
    def get_download_url(self, obj: Report) -> Optional[str]:
        """Get the absolute download URL for the report file."""
        return obj.get_download_url()


class ReportCreateSerializer(serializers.ModelSerializer):
    """
    Serializer for initiating report generation.

    Handles validation of report parameters and ensures period consistency.
    """

    class Meta:
        model = Report
        fields = [
            "report_name",
            "report_type",
            "period_start",
            "period_end",
            "format",
            "parameters",
        ]

    def validate(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Perform cross-field validation for report generation.

        Checks:
        1. Period end must be strictly after period start.
        2. Period cannot span more than 5 years (performance safety).
        3. End date cannot be more than 1 day in the future.
        4. Validate report-specific parameters.
        """
        user = self.context["request"].user
        period_start = data.get("period_start")
        period_end = data.get("period_end")
        report_type = data.get("report_type")
        parameters = data.get("parameters", {})

        # Date Range Validation
        if period_end <= period_start:
            raise serializers.ValidationError(
                {"period_end": _("Period end must be after period start.")}
            )

        # Safety Period Check (e.g., max 5 years)
        if (period_end - period_start).days > 365 * 5:
            raise serializers.ValidationError(
                {"period_start": _("Report period cannot exceed 5 years.")}
            )

        # Future Date Check
        today = timezone.now().date()
        if period_end > today + timedelta(days=1):
            raise serializers.ValidationError(
                {"period_end": _("Cannot generate reports for future dates.")}
            )

        # Parameter Validation and Property Checks
        self._validate_report_parameters(user, report_type, parameters)

        return data

    def _validate_report_parameters(
        self, user, report_type: str, parameters: Dict[str, Any]
    ) -> None:
        """
        Validate parameters based on the requested report type.
        Ensures user owns any IDs passed in parameters.
        """
        if not isinstance(parameters, dict):
            raise serializers.ValidationError(
                {"parameters": _("Parameters must be a JSON object.")}
            )

        # Check account ownership if provided
        account_id = parameters.get("account_id")
        if account_id:
            if not Account.objects.filter(id=account_id, user=user).exists():
                raise serializers.ValidationError(
                    {"parameters": _("Invalid account_id provided.")}
                )

        # Check category ownership if provided (for category-specific reports)
        category_id = parameters.get("category_id")
        if category_id:
            if not Category.objects.filter(
                models.Q(id=category_id)
                & (models.Q(user=user) | models.Q(is_system_category=True))
            ).exists():
                raise serializers.ValidationError(
                    {"parameters": _("Invalid category_id provided.")}
                )

        # Report-specific logic
        if report_type == choices.ReportType.SPENDING_BY_CATEGORY:
            # Maybe allow filtering by multiple categories
            category_ids = parameters.get("category_ids", [])
            if category_ids:
                provided_count = len(category_ids)
                actual_count = Category.objects.filter(
                    models.Q(id__in=category_ids)
                    & (models.Q(user=user) | models.Q(is_system_category=True))
                ).count()
                if provided_count != actual_count:
                    raise serializers.ValidationError(
                        {"parameters": _("One or more category IDs are invalid.")}
                    )

        elif report_type == choices.ReportType.BUDGET_VS_ACTUAL:
            budget_id = parameters.get("budget_id")
            if budget_id:
                if not Budget.objects.filter(id=budget_id, user=user).exists():
                    raise serializers.ValidationError(
                        {"parameters": _("Invalid budget_id provided.")}
                    )

    def create(self, validated_data: Dict[str, Any]) -> Report:
        """Create a report instance for the current user."""
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            raise serializers.ValidationError(_("Authentication required."))

        validated_data["user"] = request.user
        return super().create(validated_data)


def get_report_serializer(action: str):
    """
    Factory function to retrieve the appropriate Report serializer.

    Args:
        action (str): The ViewSet action name.

    Returns:
        Type[Serializer]: The serializer class.
    """
    serializers_map = {
        "list": ReportListSerializer,
        "retrieve": ReportDetailSerializer,
        "create": ReportCreateSerializer,
    }
    return serializers_map.get(action, ReportSerializer)


class RecurringTransactionSerializer(serializers.ModelSerializer):
    """
    Serializer for Recurring Transactions.
    """

    period_display = serializers.CharField(
        source="get_frequency_display", read_only=True
    )
    account_name = serializers.CharField(source="account.name", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        model = RecurringTransaction
        fields = [
            "id",
            "name",
            "amount",
            "currency",
            "transaction_type",
            "frequency",
            "period_display",
            "start_date",
            "end_date",
            "next_due_date",
            "last_generated_date",
            "is_active",
            "auto_create",
            "description",
            "account",
            "account_name",
            "category",
            "category_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["last_generated_date", "created_at", "updated_at"]

    def validate(self, attrs):
        # Validate next_due_date vs start_date
        start_date = attrs.get(
            "start_date", self.instance.start_date if self.instance else date.today()
        )
        next_due = attrs.get("next_due_date")

        if next_due and next_due < start_date:
            raise serializers.ValidationError(
                {"next_due_date": "Next due date cannot be before start date."}
            )

        return attrs


class AnalyticsDashboardSerializer(serializers.Serializer):
    """
    Serializer/Schema for the Analytics Dashboard response.
    """

    net_worth = serializers.DictField()
    monthly_cash_flow = serializers.DictField()
    safe_to_spend = serializers.DictField()
    upcoming_bills = serializers.ListField()
    budget_health = serializers.DictField()


class AnalyticsForecastSerializer(serializers.Serializer):
    """
    Schema for Forecast data points.
    """

    date = serializers.DateField()
    projected_balance = serializers.DecimalField(max_digits=18, decimal_places=2)
