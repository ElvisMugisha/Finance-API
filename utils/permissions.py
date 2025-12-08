from rest_framework import permissions
from utils import choices


class IsActiveAndVerified(permissions.BasePermission):
    """
    Allows access only to active and verified users.
    """

    message = "You do not have permission to perform this action."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            self.message = "Authentication credentials were not provided."
            return False

        if not request.user.is_active:
            self.message = "User account is disabled."
            return False

        if not request.user.is_verified:
            self.message = (
                "User account is not verified. Please verify your email address."
            )
            return False

        return True


class IsSuperAdminOrSuperUser(permissions.BasePermission):
    """
    Custom permission to only allow Super Admins or Superusers to access the view.
    User must also be active and verified.
    """

    message = "You do not have permission to perform this action."

    def has_permission(self, request, view):
        # Check if user is authenticated
        if not request.user or not request.user.is_authenticated:
            self.message = "Authentication credentials were not provided."
            return False

        # Check if user is active
        if not request.user.is_active:
            self.message = "User account is disabled."
            return False

        # Check if user is verified
        if not request.user.is_verified:
            self.message = (
                "User account is not verified. Please verify your email address."
            )
            return False

        # Check if user is a superuser
        if request.user.is_superuser:
            return True

        self.message = "You do not have permission to perform this action. Requires Super Admin privileges."
        return False
