from enum import Enum

from django.db import models
from django.utils.translation import gettext_lazy as _


class LoginStatus(models.TextChoices):
    SUCCESS = "Success", _("Success")
    FAILURE = "Failure", _("Failure")


class CodeType(models.TextChoices):
    OTP = "OTP", _("One Time Password")
    VERIFICATION = "Email_Verification", _("Email Verification")
    PASSWORD_RESET = "Password_Reset", _("Password Reset")
    LOGIN_OTP = "Login_OTP", _("Login OTP")


class Gender(models.TextChoices):
    MALE = "Male", _("Male")
    FEMALE = "Female", _("Female")
    OTHER = "Other", _("Other")


class AccountType(models.TextChoices):
    CHECKING = "Checking", _("Checking")
    SAVINGS = "Savings", _("Savings")
    CREDIT_CARD = "Credit Card", _("Credit Card")
    CASH = "Cash", _("Cash")
    INVESTMENT = "Investment", _("Investment")
    LOAN = "Loan", _("Loan")
    BANK = "Bank", _("Bank")
    WALLET = "Wallet", _("Wallet")
    OTHER = "Other", _("Other")


class TransactionType(models.TextChoices):
    INCOME = "Income", _("Income")
    EXPENSE = "Expense", _("Expense")


class TransactionStatus(models.TextChoices):
    COMPLETED = "Completed", _("Completed")
    PENDING = "Pending", _("Pending")
    RECONCILED = "Reconciled", _("Reconciled")
    CANCELED = "Canceled", _("Canceled")


class FrequencyType(models.TextChoices):
    DAILY = "Daily", _("Daily")
    WEEKLY = "Weekly", _("Weekly")
    BI_WEEKLY = "Bi-Weekly", _("Bi-Weekly")
    MONTHLY = "Monthly", _("Monthly")
    QUARTERLY = "Quarterly", _("Quarterly")
    YEARLY = "Yearly", _("Yearly")


class DayOfWeek(Enum):
    MONDAY = 1
    TUESDAY = 2
    WEDNESDAY = 3
    THURSDAY = 4
    FRIDAY = 5
    SATURDAY = 6
    SUNDAY = 7


class BudgetType(models.TextChoices):
    CATEGORY = "Category", _("Category Budget")
    OVERALL = "Overall", _("Overall Budget")
    GOAL = "Goal", _("Goal-based Budget")


class PeriodType(models.TextChoices):
    MONTHLY = "Monthly", _("Monthly")
    QUARTERLY = "Quarterly", _("Quarterly")
    YEARLY = "Yearly", _("Yearly")
    CUSTOM = "Custom", _("Custom Period")


class GoalType(models.TextChoices):
    SAVINGS = "Savings", _("Savings Goal")
    DEBT = "Debt", _("Debt Repayment")
    INVESTMENT = "Investment", _("Investment Goal")
    PURCHASE = "Purchase", _("Major Purchase")
    OTHER = "Other", _("Other")


class PriorityLevel(models.TextChoices):
    HIGH = "High", _("High Priority")
    MEDIUM = "Medium", _("Medium Priority")
    LOW = "Low", _("Low Priority")


class ReportType(models.TextChoices):
    SPENDING_BY_CATEGORY = "Spending by Category", _("Spending by Category")
    INCOME_VS_EXPENSE = "Income vs Expense", _("Income vs Expense")
    NET_WORTH = "Net Worth", _("Net Worth")
    BUDGET_VS_ACTUAL = "Budget vs Actual", _("Budget vs Actual")
    CASH_FLOW = "Cash Flow", _("Cash Flow")
    CUSTOM = "Custom", _("Custom Report")


class ReportStatus(models.TextChoices):
    PENDING = "Pending", _("Pending")
    PROCESSING = "Processing", _("Processing")
    COMPLETED = "Completed", _("Completed")
    FAILED = "Failed", _("Failed")
    CANCELLED = "Cancelled", _("Cancelled")


class ReportFormat(models.TextChoices):
    JSON = "JSON", _("JSON")
    CSV = "CSV", _("CSV")
    PDF = "PDF", _("PDF")
    EXCEL = "Excel", _("Excel")
    HTML = "HTML", _("HTML")
