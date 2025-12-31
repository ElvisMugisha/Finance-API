from django.db import models
from django.db.models import Q


class CurrencyManager(models.Manager):
    """
    Manager for Currency model with helper methods.
    """

    def active(self):
        """Return only active currencies."""
        return self.filter(is_active=True)

    def base_currency(self):
        """Return the system base currency."""
        return self.filter(is_base_currency=True).first()


class CategoryManager(models.Manager):
    """
    Manager for Category model with helper methods.
    """

    def active(self):
        """Return only active categories."""
        return self.filter(is_active=True)

    def system(self):
        """Return all system-wide categories."""
        return self.filter(is_system_category=True)

    def user_categories(self, user):
        """Return categories belonging to a specific user."""
        return self.filter(user=user)

    def visible_to(self, user):
        """
        Return categories visible to a specific user.
        Rules: Regular users see their own + system categories.
        Staff/Admin see everything.
        """
        if user.is_staff or user.is_superuser:
            return self.all()
        return self.filter(Q(user=user) | Q(is_system_category=True))
