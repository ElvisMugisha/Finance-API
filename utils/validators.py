import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from utils.loggings import setup_logging

# Initialize logger
logger = setup_logging()


class ComplexPasswordValidator:
    """
    Password validator enforcing complexity rules.

    Rules:
        - Minimum and maximum length configurable.
        - Must contain at least one letter (uppercase or lowercase).
        - Must contain at least one number.
        - Must contain at least one special character (configurable).

    Raises:
        ValidationError: If the password does not meet the requirements.
    """

    def __init__(
        self,
        min_length: int = 8,
        max_length: int = 128,
        special_characters: str = "-_@!#$%^&*",
    ):
        """
        Initialize the password validator.

        Args:
            min_length (int): Minimum password length (default 8).
            max_length (int): Maximum password length (default 128).
            special_characters (str): String of allowed special characters.
        """
        self.min_length = min_length
        self.max_length = max_length
        self.special_characters = special_characters
        self.special_pattern = f"[{re.escape(self.special_characters)}]"

    def validate(self, password: str, user=None):
        """
        Validate the password against all complexity requirements.

        Args:
            password (str): The password to validate.
            user (optional): User object (not used).

        Raises:
            ValidationError: If any validation rule fails.
        """
        try:
            if not password:
                logger.warning("Password is empty or None")
                raise ValidationError(
                    _("Password cannot be empty."), code="password_empty"
                )

            # Length checks
            if len(password) < self.min_length:
                logger.warning(f"Password too short: {len(password)} characters")
                raise ValidationError(
                    _(f"Password must be at least {self.min_length} characters long."),
                    code="password_too_short",
                )

            if len(password) > self.max_length:
                logger.warning(f"Password too long: {len(password)} characters")
                raise ValidationError(
                    _(f"Password cannot exceed {self.max_length} characters."),
                    code="password_too_long",
                )

            # Letter check
            if not re.search(r"[A-Za-z]", password):
                logger.warning("Password lacks letters")
                raise ValidationError(
                    _("Password must contain at least one letter."),
                    code="password_no_letter",
                )

            # Number check
            if not re.search(r"[0-9]", password):
                logger.warning("Password lacks numbers")
                raise ValidationError(
                    _("Password must contain at least one number."),
                    code="password_no_number",
                )

            # Special character check
            if not re.search(self.special_pattern, password):
                logger.warning(
                    f"Password lacks required special characters: {self.special_characters}"
                )
                raise ValidationError(
                    _(
                        f"Password must contain at least one special character: {', '.join(self.special_characters)}"
                    ),
                    code="password_no_special_character",
                )

            logger.info("Password validation passed successfully")

        except ValidationError as ve:
            raise ve

        except Exception as e:
            logger.exception(f"Unexpected error during password validation: {str(e)}")
            raise ValidationError(
                _("An unexpected error occurred during password validation.")
            )

    def get_help_text(self):
        """
        Return a description of password requirements.

        Returns:
            str: Help text describing rules.
        """
        return _(
            f"Password must be between {self.min_length} and {self.max_length} characters long, "
            f"contain at least one letter, one number, and one special character "
            f"({', '.join(self.special_characters)})."
        )
