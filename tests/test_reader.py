from pathlib import Path

from bendy.reader import read_manifest


def manifest(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "manifest.py"
    p.write_text(content)
    return p


def test_basic_aggregate(tmp_path):
    p = manifest(
        tmp_path,
        """
from bendy import Aggregate

class User(Aggregate):
    name: str
    age: int
""",
    )
    result = read_manifest(p)
    assert not result.errors
    assert len(result.aggregates) == 1
    agg = result.aggregates[0]
    assert agg.name == "User"
    assert [f.name for f in agg.fields] == ["name", "age"]


def test_file_not_found(tmp_path):
    result = read_manifest(tmp_path / "nope.py")
    assert any("not found" in e for e in result.errors)


def test_syntax_error(tmp_path):
    p = manifest(tmp_path, "class Broken(:\n    pass")
    result = read_manifest(p)
    assert any("syntax error" in e for e in result.errors)


def test_unknown_type_collected(tmp_path):
    p = manifest(
        tmp_path,
        """
from bendy import Aggregate

class Order(Aggregate):
    data: list
    name: str
""",
    )
    result = read_manifest(p)
    # error collected, but other fields still parsed
    assert any("unsupported type" in e for e in result.errors)
    agg = result.aggregates[0]
    assert any(f.name == "name" for f in agg.fields)


def test_enum_detected(tmp_path):
    p = manifest(
        tmp_path,
        """
from enum import Enum
from bendy import Aggregate

class Status(Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"

class Order(Aggregate):
    status: Status = Status.ACTIVE
""",
    )
    result = read_manifest(p)
    assert not result.errors
    assert len(result.enums) == 1
    assert result.enums[0].name == "Status"
    assert result.enums[0].values == [("ACTIVE", "active"), ("ARCHIVED", "archived")]


def test_value_object_detected(tmp_path):
    p = manifest(
        tmp_path,
        """
from bendy import Aggregate, ValueObject

class Address(ValueObject):
    street: str
    city: str

class User(Aggregate):
    address: Address
""",
    )
    result = read_manifest(p)
    assert not result.errors
    assert len(result.value_objects) == 1
    assert result.value_objects[0].name == "Address"


def test_default_use_cases(tmp_path):
    p = manifest(
        tmp_path,
        """
from bendy import Aggregate

class Order(Aggregate):
    name: str
""",
    )
    result = read_manifest(p)
    assert result.aggregates[0].use_cases == ["create", "get"]


def test_meta_use_cases(tmp_path):
    p = manifest(
        tmp_path,
        """
from bendy import Aggregate

class Order(Aggregate):
    name: str

    class Meta:
        use_cases = ["create", "get", "delete"]
""",
    )
    result = read_manifest(p)
    assert not result.errors
    assert result.aggregates[0].use_cases == ["create", "get", "delete"]


def test_invalid_use_case_collected(tmp_path):
    p = manifest(
        tmp_path,
        """
from bendy import Aggregate

class Order(Aggregate):
    name: str

    class Meta:
        use_cases = ["create", "fly"]
""",
    )
    result = read_manifest(p)
    assert any("unknown operations" in e for e in result.errors)


def test_multiple_aggregates(tmp_path):
    p = manifest(
        tmp_path,
        """
from bendy import Aggregate

class User(Aggregate):
    email: str

class Product(Aggregate):
    name: str
    price: float
""",
    )
    result = read_manifest(p)
    assert not result.errors
    assert {a.name for a in result.aggregates} == {"User", "Product"}
