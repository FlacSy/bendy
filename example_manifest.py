from datetime import datetime
from decimal import Decimal
from enum import Enum

from bendy import Aggregate, Field, ValueObject, auto_now


class OrderStatus(Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    SHIPPED = "shipped"
    CANCELLED = "cancelled"


class Address(ValueObject):
    street: str
    city: str
    zip_code: str
    country: str = "RU"


class Order(Aggregate):
    customer_id: str
    total_amount: Decimal
    status: OrderStatus = OrderStatus.PENDING
    shipping_address: Address
    notes: str | None = None
    created_at: datetime = auto_now

    class Meta:
        use_cases = ["create", "get", "update", "delete", "list"]


class User(Aggregate):
    email: str = Field(unique=True, index=True)
    username: str = Field(max_length=50, index=True)
    is_active: bool = True
    bio: str | None = None
