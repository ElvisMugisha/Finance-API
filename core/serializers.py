from rest_framework import serializers

from .models import Currency


class CurrencySerializer(serializers.ModelSerializer):
    """
    Serializer for Currency model.
    """

    class Meta:
        model = Currency
        fields = [
            "id",
            "code",
            "name",
            "symbol",
            "exchange_rate",
            "is_active",
            "updated_at",
        ]
        read_only_fields = ["id", "updated_at"]

    def validate_code(self, value):
        """Ensure currency code is uppercase."""
        return value.upper()
