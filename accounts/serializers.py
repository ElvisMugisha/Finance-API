from rest_framework import serializers

from core.models import Currency
from core.serializers import CurrencySerializer
from utils import loggings

from .models import Account

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
