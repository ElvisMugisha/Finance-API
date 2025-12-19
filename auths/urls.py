from django.urls import path

from . import views

urlpatterns = [
    path("register/", views.UserRegistrationView.as_view(), name="register"),
    path("verify-email/", views.EmailVerificationView.as_view(), name="verify-email"),
    path("resend-otp/", views.ResendOTPView.as_view(), name="resend-otp"),
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
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
        "password/reset/set-new/",
        views.PasswordResetSetNewView.as_view(),
        name="password-reset-set-new",
    ),
    path("refresh-token/", views.TokenRefreshAPIView.as_view(), name="refresh-token"),
    path("users/", views.UserListView.as_view(), name="user-list"),
    path("profile/me/", views.UserMeView.as_view(), name="me"),
    path(
        "profile/manage/", views.UserProfileManageView.as_view(), name="manage-profile"
    ),
]
