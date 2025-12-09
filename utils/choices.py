from django.db import models
from django.utils.translation import gettext_lazy as _


class CodeType(models.TextChoices):
    OTP = "OTP", _("One Time Password")
    VERIFICATION = "Email_Verification", _("Email Verification")
    PASSWORD_RESET = "Password_Reset", _("Password Reset")
    LOGIN_OTP = "Login_OTP", _("Login OTP")


class Gender(models.TextChoices):
    MALE = "Male", _("Male")
    FEMALE = "Female", _("Female")
    OTHER = "Other", _("Other")
