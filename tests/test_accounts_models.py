import pytest
from decimal import Decimal
from accounts.models import Account
from core.models import Currency
from utils import choices


@pytest.mark.django_db
class TestAccountModel:
    """Test suite for Account model."""

    @pytest.fixture
    def user(self, django_user_model):
        return django_user_model.objects.create_user(
            email="test@example.com", password="password"
        )

    @pytest.fixture
    def currency(self):
        return Currency.objects.create(
            code="USD", name="Dollar", symbol="$", exchange_rate=1.0
        )

    def test_create_account(self, user, currency):
        """Test creating a valid account."""
        account = Account.objects.create(
            user=user,
            currency=currency,
            name="Main Checking",
            account_type=choices.AccountType.CHECKING,
            initial_balance=Decimal("1000.00"),
        )
        assert account.pk is not None
        assert account.current_balance == Decimal("0.00")  # Default is 0 unless set?
        # Wait, model default is 0.00. We didn't set current_balance in create() above.
        # But logically, should create() set current = initial?
        # The serializer does this. The model doesn't enforce it automatically in save().

    def test_primary_account_switching(self, user, currency):
        """Test that setting a new primary account unsets the old one."""
        acc1 = Account.objects.create(
            user=user, currency=currency, name="Acc 1", is_primary=True
        )
        acc2 = Account.objects.create(
            user=user, currency=currency, name="Acc 2", is_primary=True
        )

        acc1.refresh_from_db()
        assert acc2.is_primary is True
        assert acc1.is_primary is False
