from rest_framework import serializers

from utils import loggings

from .models import Category, Currency

logger = loggings.setup_logging()


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


class CategorySerializer(serializers.ModelSerializer):
    """
    Serializer for Category objects.

    Responsibilities:
    - Handles validation of user-defined vs system-defined categories.
    - Ensures names are unique per user & category type.
    - Applies simple, predictable field-level validation (KISS).
    - Logs meaningful debug/error messages for maintainability (DRY).
    - Prevents users from creating or modifying system categories directly.
    """

    # Readable parent information in responses
    parent_name = serializers.CharField(source="parent.name", read_only=True)

    class Meta:
        model = Category
        fields = [
            "id",
            "user",
            "parent",
            "parent_name",
            "name",
            "description",
            "category_type",
            "is_system_category",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "is_system_category",  # users should not toggle this
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        """
        Perform high-level validation before saving the category.

        Ensures:
        - Regular users cannot create system categories.
        - Category name uniqueness is preserved per (user, category_type).
        - Parent category belongs to the same user (hierarchy integrity).
        """
        request = self.context.get("request")
        user = request.user if request else None

        parent = attrs.get("parent")
        name = attrs.get("name", getattr(self.instance, "name", None))
        category_type = attrs.get(
            "category_type", getattr(self.instance, "category_type", None)
        )

        # Prevent users from creating system categories
        if attrs.get("is_system_category") is True:
            logger.warning(
                "User '%s' attempted to create or modify a system category.",
                user.id if user else "unknown",
            )
            raise serializers.ValidationError(
                {
                    "is_system_category": "You are not allowed to create system categories."
                }
            )

        # Ensure parent category belongs to the same user
        if parent:
            if parent.user != user and parent.user is not None:
                logger.error(
                    "Hierarchy violation: User '%s' attempted to assign a parent category "
                    "owned by another user.",
                    user.id if user else "unknown",
                )
                raise serializers.ValidationError(
                    {"parent": "Parent category must belong to the same user."}
                )

        # Enforce uniqueness manually for clear error messages
        if user:
            exists = (
                Category.objects.filter(
                    user=user,
                    name=name,
                    category_type=category_type,
                )
                .exclude(id=self.instance.id if self.instance else None)
                .exists()
            )

            if exists:
                logger.info(
                    "Duplicate category prevented: User '%s' attempted "
                    "to create category '%s' (%s) that already exists.",
                    user.id,
                    name,
                    category_type,
                )
                raise serializers.ValidationError(
                    {
                        "name": "A category with this name already exists for this type.",
                        "category_type": "Duplicate category type for this name.",
                    }
                )

        return attrs

    def create(self, validated_data):
        """
        Creates a new category while applying business rules.

        Automatically assigns the request user as the category owner.
        """
        try:
            request = self.context.get("request")
            user = request.user if request else None

            validated_data["user"] = user

            category = Category.objects.create(**validated_data)

            logger.debug(
                "Category created successfully: id=%s user=%s name='%s'",
                category.id,
                user.id if user else None,
                category.name,
            )

            return category

        except Exception as exc:
            logger.exception("Error creating category: %s", str(exc))
            raise serializers.ValidationError(
                "An unexpected error occurred while creating the category."
            )

    def update(self, instance, validated_data):
        """
        Updates an existing category while maintaining rules:
        - System categories cannot be modified by users.
        - Only mutable fields are updated.
        """
        if instance.is_system_category:
            logger.warning(
                "Update prevented: User '%s' attempted to modify system category '%s' (%s).",
                instance.user.id if instance.user else "SYSTEM",
                instance.name,
                instance.id,
            )
            raise serializers.ValidationError("System categories cannot be modified.")

        try:
            for field, value in validated_data.items():
                setattr(instance, field, value)

            instance.save()

            logger.debug(
                "Category updated successfully: id=%s name='%s'",
                instance.id,
                instance.name,
            )

            return instance

        except Exception as exc:
            logger.exception("Error updating category: %s", str(exc))
            raise serializers.ValidationError(
                "An unexpected error occurred while updating the category."
            )
