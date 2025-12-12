from datetime import date
from decimal import Decimal

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from core.models import Category, Currency
from core.serializers import CurrencySerializer
from utils import choices, loggings

from .models import Account, Transaction

logger = loggings.setup_logging()


class AccountSerializer(serializers.ModelSerializer):
    """
    Serializer for Account model.
    Handles creation and retrieval of financial accounts.
    """

    # Use PrimaryKey related field for writing (inputting ID)
    currency_id = serializers.PrimaryKeyRelatedField(
        queryset=Currency.objects.filter(is_active=True),
        source="currency",
        write_only=True,
        help_text="ID of the currency for this account",
    )

    # Use CurrencySerialize for reading (nesting details)
    currency = CurrencySerializer(read_only=True)

    account_type = serializers.ChoiceField(
        choices=choices.AccountType.choices,
        required=True,
    )

    class Meta:
        model = Account
        fields = [
            "id",
            "name",
            "account_type",
            "account_number",
            "bank_name",
            "currency_id",  # Write
            "currency",  # Read
            "initial_balance",
            "current_balance",
            "is_primary",
            "is_active",
            "institution_data",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "current_balance", "created_at", "updated_at"]

    def validate_initial_balance(self, value):
        """
        Ensure initial balance is reasonable (e.g., not overly negative unless it's a loan).
        """
        # We can add more specific logic here based on account_type if needed.
        return value

    def create(self, validated_data):
        """
        Create account and assign to current user.
        """
        user = self.context["request"].user

        # Ensure 'user' is passed to create
        validated_data["user"] = user

        # Calculate current_balance from initial_balance
        # (Initially they are the same before any transactions)
        validated_data["current_balance"] = validated_data.get("initial_balance", 0)

        logger.info(
            f"Creating account '{validated_data.get('name')}' for user {user.email}"
        )

        try:
            return super().create(validated_data)
        except Exception as e:
            logger.error(f"Failed to create account: {e}")
            raise e

    def update(self, instance, validated_data):
        """
        Handle updates.
        Note: Changing currency or initial_balance after creation might have side effects
        if transactions exist. For now, we allow it but log it.
        """
        logger.info(f"Updating account {instance.id} for user {instance.user.email}")

        # If initial_balance changes, we might need to recalculate current_balance logic
        # For simplicity in this phase, we update current_balance if initial_balance changes
        # assuming no transactions yet. Ideally, current_balance is Sum(transactions) + initial.

        if (
            "initial_balance" in validated_data
            and validated_data["initial_balance"] != instance.initial_balance
        ):
            diff = validated_data["initial_balance"] - instance.initial_balance
            instance.current_balance += diff
            logger.info(
                f"Adjusted current balance by {diff} due to initial balance update"
            )

        return super().update(instance, validated_data)


class TransactionSerializer(serializers.ModelSerializer):
    """
    Serializer for Transaction model.

    Features:
    - Validates all monetary and currency conversion logic.
    - Ensures category/account belong to the user (unless staff).
    - Provides computed read-only fields for convenience in UI.
    - Full model-level validation + serializer-level validation.
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
