from rest_framework import permissions
from django.utils.translation import gettext_lazy as _
from utils import loggings

# Initialize logger
logger = loggings.setup_logging()


class BaseUserPermission(permissions.BasePermission):
    """Base permission class with common user checks."""

    def _check_user(self, request):
        """Common user validation checks."""
        if not request.user or not request.user.is_authenticated:
            self.message = "Authentication credentials were not provided."
            return False

        if not request.user.is_active:
            self.message = "User account is not active."
            return False

        if not getattr(request.user, "is_verified", True):
            self.message = "User account is not verified."
            return False

        return True


class IsActiveAndVerified(BaseUserPermission):
    """Allows access only to active and verified users."""

    message = "You do not have permission to perform this action."

    def has_permission(self, request, view):
        return self._check_user(request)


class IsStaffOrAdmin(BaseUserPermission):
    """Allows access to staff or admin users."""

    message = "Staff or admin privileges required."

    def has_permission(self, request, view):
        if not self._check_user(request):
            return False
        return request.user.is_staff or request.user.is_superuser


class IsAdminOnly(BaseUserPermission):
    """Allows access only to admin users (superusers)."""

    message = "Admin privileges required."

    def has_permission(self, request, view):
        if not self._check_user(request):
            return False
        return request.user.is_superuser


class IsAdminOrReadOnly(BaseUserPermission):
    """Allows read access to all, write access only to admins."""

    message = "Admin privileges required for write operations."

    def has_permission(self, request, view):
        if not self._check_user(request):
            return False

        # Allow all safe methods (GET, HEAD, OPTIONS)
        if request.method in permissions.SAFE_METHODS:
            return True

        # Only admins can write
        return request.user.is_superuser


class IsOwnerOrAdmin(permissions.BasePermission):
    """
    Object-level permission:
    - Admins (superuser or staff) have full access.
    - Regular users can only access objects they own.
    """

    message = "You do not have permission to access or modify this resource."

    def has_permission(self, request, view):
        # Basic auth check
        if not request.user or not request.user.is_authenticated:
            self.message = "Authentication credentials were not provided."
            return False

        if not request.user.is_active:
            self.message = "User account is not active."
            return False

        if not request.user.is_verified:
            self.message = "User account is not verified."
            return False

        return True

    def has_object_permission(self, request, view, obj):
        user = request.user

        # Admins always allowed
        if user.is_superuser or user.is_staff:
            return True

        # Must have a "user" attribute
        if not hasattr(obj, "user"):
            self.message = "This object does not have an owner field."
            return False

        # Regular users can access only their own objects
        return obj.user == user


class CategoryPermission(BaseUserPermission):
    """
    Custom permission for Category operations.

    Rules:
    - All authenticated users can list their own categories + system categories
    - Regular users can only CRUD their own categories
    - Staff/Admin can CRUD any category (including system categories)
    """

    message = "You do not have permission to perform this action."

    def has_permission(self, request, view):
        """Check general permission for the view."""
        if not self._check_user(request):
            return False

        # Everyone can list and retrieve
        if view.action in ["list", "retrieve", "tree"]:
            return True

        # Everyone can create (handled in has_object_permission for update/delete)
        if view.action == "create":
            return True

        # Bulk operations require staff/admin
        if view.action == "bulk_update":
            return request.user.is_staff or request.user.is_superuser

        return True

    def has_object_permission(self, request, view, obj):
        """Check permission for specific category object."""
        user = request.user

        # Staff/Admin can do anything
        if user.is_staff or user.is_superuser:
            return True

        # Regular users can only modify their own categories
        if obj.user == user:
            return True

        # Users cannot modify system categories or other users' categories
        if obj.is_system_category:
            logger.warning(
                f"User {user.id} attempted to modify system category {obj.id}"
            )
            self.message = "Cannot modify system categories."
            return False

        if obj.user and obj.user != user:
            logger.warning(
                f"User {user.id} attempted to modify another user's category {obj.id}"
            )
            self.message = "Cannot modify another user's categories."
            return False

        return False
