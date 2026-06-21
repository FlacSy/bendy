# Bendykit

Code generator for Python backends following DDD principles.

Describe your domain in a single Python file, get a full stack: domain models, repository, use cases, DTOs and FastAPI routers. Architecture is Onion, no ActiveRecord, strict layer separation.

## Installation

```bash
pip install bendykit
```

## Usage

```python
# manifest.py
from decimal import Decimal
from enum import Enum
from typing import Optional
from datetime import datetime

from bendy import Aggregate, ValueObject, Field, auto_now


class OrderStatus(Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"


class Address(ValueObject):
    street: str
    city: str
    country: str = "US"


class Order(Aggregate):
    customer_id: str
    total_amount: Decimal
    status: OrderStatus = OrderStatus.PENDING
    shipping_address: Address
    notes: Optional[str] = None
    created_at: datetime = auto_now

    class Meta:
        use_cases = ["create", "get", "update", "delete", "list"]
```

```bash
bendykit manifest.py ./src
```

Generates for each aggregate:

```
src/order/
├── domain/
│   ├── models.py        # @dataclass Order
│   ├── enums.py         # OrderStatus(Enum)
│   ├── value_objects.py # @dataclass(frozen=True) Address
│   └── repository.py    # OrderRepository(ABC)
├── application/
│   ├── dtos.py          # OrderCreate / OrderUpdate / OrderResponse
│   └── use_cases.py     # Create/Get/Update/Delete/ListOrderUseCase
├── presentation/
│   └── router.py        # FastAPI router
└── infrastructure/
    ├── models.py        # OrderModel(Base) — SQLAlchemy 2.0
    └── repository.py    # SqlalchemyOrderRepository + mapper
```

## Types

| Manifest            | Python        | SQLAlchemy      |
|---------------------|---------------|-----------------|
| `str`               | `str`         | `String`        |
| `int`               | `int`         | `Integer`       |
| `float`             | `float`       | `Float`         |
| `bool`              | `bool`        | `Boolean`       |
| `Decimal`           | `Decimal`     | `Numeric`       |
| `datetime`          | `datetime`    | `DateTime`      |
| `date`              | `date`        | `Date`          |
| `UUID`              | `UUID`        | `String`        |
| `Optional[X]`       | `Optional[X]` | `nullable=True` |
| `MyEnum(Enum)`      | `MyEnum`      | `String` + mapper |
| `MyVO(ValueObject)` | `MyVO`        | `JSON`          |

## Manifest primitives

- **`auto_now`** — value is set server-side, excluded from Create/Update DTOs
- **`Field(unique=True, index=True, max_length=N)`** — column metadata
- **`ValueObject`** — nested object without id, stored as JSON
- **`Meta.use_cases`** — operations to generate: `create`, `get`, `update`, `delete`, `list` (default: `create`, `get`)

## Roadmap

- [x] Phase I — CLI generator
- [x] Phase II — Python manifests (Enum, ValueObject, Field, auto_now, Meta)
- [ ] Phase III — `bendykit.runtime`: Unit of Work, Domain Events

## License

MIT
