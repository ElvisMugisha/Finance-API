from rest_framework import serializers
from django.contrib.auth.password_validation import validate_password
from rest_framework.exceptions import ValidationError

from .models import User
from utils import loggings

# Initialize logger
logger = loggings.setup_logging()


class UserRegistrationSerializer(serializers.ModelSerializer):
    """
    Serializer for User Registration.

    This serializer handles the creation of a new user. It validates the
    input data, ensures passwords match, and checks for password complexity.
    """

    password = serializers.CharField(
        write_only=True, required=True, style={"input_type": "password"}
    )
    confirm_password = serializers.CharField(
        write_only=True, required=True, style={"input_type": "password"}
    )

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "password",
            "confirm_password",
            "is_active",
            "is_verified",
            "last_activity",
            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "id",
            "username",
            "is_active",
            "is_verified",
            "last_activity",
            "created_at",
            "updated_at",
        ]

    def validate_email(self, value):
        """
        Normalize the email to lowercase and check for uniqueness.
        """
        email = value.lower()
        if User.objects.filter(email=email).exists():
            logger.warning("Registration failed: Email already exists.")
            raise serializers.ValidationError("A user with this email already exists.")
        return email

    def validate(self, attrs):
        """
        Validate the data passed to the serializer.

        Checks:
        1. Passwords match.
        2. Password complexity.
        """
        logger.debug("Starting validation for user registration.")

        password = attrs.get("password")
        confirm_password = attrs.get("confirm_password")

        if password != confirm_password:
            logger.warning("Registration failed: Passwords do not match.")
            raise ValidationError({"password": "Passwords do not match."})

        try:
            validate_password(password)
        except ValidationError as e:
            logger.warning(f"Registration failed: Password complexity error - {e}")
            raise serializers.ValidationError({"password": list(e.messages)})

        return attrs

    def create(self, validated_data):
        """
        Create a new user instance.

        Removes 'confirm_password' from the data and uses the UserManager
        to create the user safely.
        """
        logger.info(f"Creating new user with email: {validated_data.get('email')}")

        validated_data.pop("confirm_password")

        try:
            user = User.objects.create_user(**validated_data)
            logger.info(f"User [{user.username}] created successfully")
            return user
        except Exception as e:
            logger.error(f"Error creating user: {str(e)}")
            raise ValidationError({"error": "Unable to create user. Please try again."})


class UserSerializer(serializers.ModelSerializer):
    """
    Serializer for the User model.

    This serializer is used to retrieve and update user information.
    It includes fields for user identification, profile details, and status.
    """

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "is_active",
            "is_verified",
            "last_activity",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "email",
            "is_active",
            "is_verified",
            "last_activity",
            "created_at",
            "updated_at",
        ]

    def update(self, instance, validated_data):
        """
        Update and return an existing `User` instance, given the validated data.
        """
        logger.info(f"Updating user [{instance.username}] info")
        return super().update(instance, validated_data)
