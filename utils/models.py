import uuid

from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, models, transaction

from utils import loggings

# Initialize logger
logger = loggings.setup_logging()


class BaseModel(models.Model):
    """
    Abstract base model providing common fields and methods.

    Following DRY principle to avoid repeating common patterns.
    """

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        """
        Enhanced save with validation and error handling.

        Args:
            *args: Variable length argument list
            **kwargs: Arbitrary keyword arguments

        Raises:
            ValidationError: If model validation fails
            DatabaseError: If database operation fails
        """
        try:
            self.full_clean()

            with transaction.atomic():
                super().save(*args, **kwargs)
                logger.debug(f"{self.__class__.__name__} saved: {self.id}")

        except ValidationError as ve:
            logger.error(
                f"Validation failed for {self.__class__.__name__} {self.id}: {ve}"
            )
            raise
        except (DatabaseError, IntegrityError) as de:
            logger.error(
                f"Database error saving {self.__class__.__name__} {self.id}: {de}"
            )
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error saving {self.__class__.__name__} {self.id}: {e}"
            )
            raise

    @classmethod
    def get_for_user(cls, user_id: uuid.UUID, **filters) -> models.QuerySet:
        """
        Get objects for a specific user with optional filters.

        Args:
            user_id: User UUID
            **filters: Additional filter parameters

        Returns:
            Filtered QuerySet
        """
        return cls.objects.filter(user_id=user_id, **filters)
