from django.urls import path

from . import views

urlpatterns = [
    path("register/", views.UserRegistrationView.as_view(), name="register"),
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path(
        "token/refresh/", views.CustomTokenRefreshView.as_view(), name="token-refresh"
    ),
    path("verify-email/", views.EmailVerificationView.as_view(), name="verify-email"),
    path("resend-otp/", views.ResendOTPView.as_view(), name="resend-otp"),
    path("users/", views.UserListView.as_view(), name="user-list"),
    path("profile/", views.UserProfileView.as_view(), name="user-profile"),
    path(
        "profile/manage/", views.UserProfileManageView.as_view(), name="manage-profile"
    ),
    path(
        "password/change/", views.PasswordChangeView.as_view(), name="change-password"
    ),
    path(
        "password/reset/request/",
        views.PasswordResetRequestView.as_view(),
        name="password-reset-request",
    ),
    path(
        "password/reset/verify/",
        views.PasswordResetVerifyView.as_view(),
        name="password-reset-verify",
    ),
    path(
        "password/reset/confirm/",
        views.PasswordResetConfirmView.as_view(),
        name="password-reset-confirm",
    ),
]
