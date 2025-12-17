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
            logger.warning(self.message)
            return False

        if not request.user.is_active:
            self.message = "User account is not active."
            logger.warning(self.message)
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


class IsOwnerOrAdmin(BaseUserPermission):
    """
    Custom permission to only allow account owners or admins to access.

    Rules:
    - Superusers and staff can access any account
    - Regular users can only access their own accounts
    - All users must be active and verified
    """

    message = "You do not have permission to access this account."

    def has_object_permission(self, request, view, obj):
        """Check permission for specific account object."""
        if not self._check_user(request):
            return False

        # Staff/Admin can do anything
        if request.user.is_staff or request.user.is_superuser:
            return True

        # Regular users can only access their own accounts
        if obj.user == request.user:
            return True

        logger.warning(
            f"Permission denied: User {request.user.id} attempted to access "
            f"account {obj.id} owned by user {obj.user.id}"
        )
        return False


class CategoryPermission(BaseUserPermission):
    """
    Permission class for category operations.

    Rules:
    - All authenticated users can create/view categories
    - Users can only modify/delete their own categories
    - Staff/Admin can modify/delete any category
    - System categories are read-only for regular users
    """

    message = "You do not have permission to perform this action."

    def has_permission(self, request, view):
        """Check general permission for the view."""
        if not self._check_user(request):
            return False

        return True

    def has_object_permission(self, request, view, obj):
        """Check object-level permission."""
        user = request.user

        # Staff/Admin have full access
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
