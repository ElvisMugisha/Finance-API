from enum import Enum

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
    DAILY = "daily", _("Daily")
    WEEKLY = "weekly", _("Weekly")
    BI_WEEKLY = "bi_weekly", _("Bi-Weekly")
    MONTHLY = "monthly", _("Monthly")
    QUARTERLY = "quarterly", _("Quarterly")
    YEARLY = "yearly", _("Yearly")


class DayOfWeek(Enum):
    MONDAY = 1
    TUESDAY = 2
    WEDNESDAY = 3
    THURSDAY = 4
    FRIDAY = 5
    SATURDAY = 6
    SUNDAY = 7


class BudgetType(models.TextChoices):
    CATEGORY = "category", _("Category Budget")
    OVERALL = "overall", _("Overall Budget")
    GOAL = "goal", _("Goal-based Budget")


class PeriodType(models.TextChoices):
    MONTHLY = "monthly", _("Monthly")
    QUARTERLY = "quarterly", _("Quarterly")
    YEARLY = "yearly", _("Yearly")
    CUSTOM = "custom", _("Custom Period")


class GoalType(models.TextChoices):
    SAVINGS = "savings", _("Savings Goal")
    DEBT = "debt", _("Debt Repayment")
    INVESTMENT = "investment", _("Investment Goal")
    PURCHASE = "purchase", _("Major Purchase")
    OTHER = "other", _("Other")


class PriorityLevel(models.TextChoices):
    HIGH = "high", _("High Priority")
    MEDIUM = "medium", _("Medium Priority")
    LOW = "low", _("Low Priority")


class ReportType(models.TextChoices):
    SPENDING_BY_CATEGORY = "spending_by_category", _("Spending by Category")
    INCOME_VS_EXPENSE = "income_vs_expense", _("Income vs Expense")
    NET_WORTH = "net_worth", _("Net Worth")
    BUDGET_VS_ACTUAL = "budget_vs_actual", _("Budget vs Actual")
    CASH_FLOW = "cash_flow", _("Cash Flow")
    CUSTOM = "custom", _("Custom Report")


class ReportStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    PROCESSING = "processing", _("Processing")
    COMPLETED = "completed", _("Completed")
    FAILED = "failed", _("Failed")
    CANCELLED = "cancelled", _("Cancelled")


class ReportFormat(models.TextChoices):
    JSON = "json", _("JSON")
    CSV = "csv", _("CSV")
    PDF = "pdf", _("PDF")
    EXCEL = "excel", _("Excel")
    HTML = "html", _("HTML")
