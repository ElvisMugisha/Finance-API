from rest_framework import permissions


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
            self.message = "User account is not active."
            return False

        if not request.user.is_verified:
            self.message = (
                "User account is not verified. Please verify your account first."
            )
            return False

        return True


class IsAdminUser(permissions.BasePermission):
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


class CategoryPermission(permissions.BasePermission):
    """
    Enforces strict access control for Category CRUD operations.

    Rules:
    - Superusers & staff can CRUD ALL categories (including system categories).
    - Regular users can only CRUD:
        - Categories they own (user = request.user)
        - And cannot CRUD system categories.
    """

    def has_object_permission(self, request, view, obj):
        user = request.user

        # Admins can do everything
        if user.is_staff or user.is_superuser:
            return True

        # Regular users cannot access system categories
        if obj.is_system_category:
            return False

        # Regular users can only access their own categories
        return obj.user == user

    def has_permission(self, request, view):
        # For create, the serializer will set user automatically;
        # only admins can create system categories (checked later).
        return request.user and request.user.is_authenticated
