from django.contrib.auth.models import BaseUserManager
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils.translation import gettext_lazy as _

from utils import loggings

logger = loggings.setup_logging()


class UserManager(BaseUserManager):
    """
    Custom user manager responsible for:
    - User creation
    - Superuser creation
    - Email normalization & validation
    """

    def _validate_email(self, email: str) -> str:
        if not email:
            raise ValueError(_("Email address is required"))

        # Normalize the email using Django's built-in
        email = self.normalize_email(email)

        # Also lowercase the entire email if desired
        email = email.lower()

        try:
            validate_email(email)
        except ValidationError as exc:
            logger.warning("Invalid email provided", extra={"email": email})
            raise ValueError(_("Invalid email address")) from exc

        return email

    def _generate_unique_username(self, first_name: str, last_name: str) -> str:
        base = f"{first_name.lower().strip()}.{last_name.lower().strip()}".replace(
            " ", ""
        )

        # Remove any characters that aren't alphanumeric, dot, dash, or underscore
        import re

        base = re.sub(r"[^\w\.\-]", "", base)

        candidate = base
        counter = 1

        while self.model.objects.filter(username=candidate).exists():
            candidate = f"{base}{counter}"
            counter += 1

        return candidate

    @transaction.atomic
    def create_user(self, email, first_name, last_name, password=None, **extra_fields):
        """
        Create and persist a regular user.
        """
        email = self._validate_email(email)

        if not first_name or not last_name:
            raise ValueError(_("First name and last name are required"))

        username = extra_fields.pop(
            "username",
            self._generate_unique_username(first_name, last_name),
        )

        try:
            user = self.model(
                email=email,
                username=username,
                first_name=first_name,
                last_name=last_name,
                **extra_fields,
            )

            user.set_password(password)
            user.full_clean()
            user.save(using=self._db)

        except IntegrityError as exc:
            logger.error("Failed to create user", exc_info=True)
            raise ValueError(_("User creation failed")) from exc

        logger.info("User created", extra={"user_id": user.id})
        return user

    def create_superuser(
        self, email, first_name, last_name, password=None, **extra_fields
    ):
        """
        Create and persist a superuser.
        """
        if not password:
            raise ValueError(_("Superusers must have a password"))

        extra_fields.update(
            {
                "is_staff": True,
                "is_superuser": True,
                "is_active": True,
                "is_verified": True,
            }
        )

        return self.create_user(
            email=email,
            first_name=first_name,
            last_name=last_name,
            password=password,
            **extra_fields,
        )
