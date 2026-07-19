import ast
from pathlib import Path

from bendy.generator import _AGGREGATE_TEMPLATES, _CONDITIONAL_TEMPLATES, generate
from bendy.reader import read_manifest


def manifest(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "manifest.py"
    p.write_text(content)
    return p


def run(tmp_path: Path, content: str) -> tuple[Path, list[str]]:
    p = manifest(tmp_path, content)
    result = read_manifest(p)
    assert not result.errors, result.errors
    out = tmp_path / "out"
    agg_results = generate(result, out)
    errors = [
        f"{fr.relative_path}: {fr.error}" for ar in agg_results for fr in ar.files if fr.error
    ]
    return out, errors


SIMPLE = """
from bendy import Aggregate

class Product(Aggregate):
    name: str
    price: float
    in_stock: bool = True
"""

FULL = """
from decimal import Decimal
from enum import Enum
from typing import Optional
from datetime import datetime

from bendy import Aggregate, ValueObject, Field, auto_now


class Status(Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class Dimensions(ValueObject):
    width: float
    height: float


class Product(Aggregate):
    name: str = Field(max_length=200, index=True)
    price: Decimal
    status: Status = Status.ACTIVE
    dimensions: Dimensions
    description: Optional[str] = None
    created_at: datetime = auto_now

    class Meta:
        use_cases = ["create", "get", "update", "delete", "list"]
"""

WITH_UUID = """
from uuid import UUID

from bendy import Aggregate

class Order(Aggregate):
    customer_id: UUID
    total: float
"""


def test_all_files_created(tmp_path):
    out, errors = run(tmp_path, SIMPLE)
    assert not errors
    for name, path in _AGGREGATE_TEMPLATES.items():
        if name in _CONDITIONAL_TEMPLATES:
            continue  # SIMPLE has no enums — domain/enums.py is not emitted
        assert (out / "product" / path).exists()


def test_enums_file_only_created_when_enums_present(tmp_path):
    out, _ = run(tmp_path, SIMPLE)
    assert not (out / "product" / "domain" / "enums.py").exists()

    out, _ = run(tmp_path, FULL)
    assert (out / "product" / "domain" / "enums.py").exists()


def test_enum_file_content(tmp_path):
    out, _ = run(tmp_path, FULL)
    content = (out / "product/domain/enums.py").read_text()
    assert "class Status(Enum):" in content
    assert "ACTIVE = 'active'" in content
    assert "ARCHIVED = 'archived'" in content


def test_infra_repository_relative_imports_are_valid(tmp_path):
    out, _ = run(tmp_path, FULL)
    content = (out / "product/infrastructure/repository.py").read_text()
    assert "from ...domain" not in content
    assert "from ..domain.models import Product" in content
    assert "from ..domain.repository import ProductRepository" in content
    assert "from ..domain.enums import Status" in content


def test_infra_models_uuid_import(tmp_path):
    out, errors = run(tmp_path, WITH_UUID)
    assert not errors
    content = (out / "order/infrastructure/models.py").read_text()
    assert "from uuid import UUID" in content
    assert "customer_id: Mapped[UUID]" in content


def test_generated_files_are_valid_python(tmp_path):
    out, errors = run(tmp_path, SIMPLE)
    assert not errors
    for py_file in out.rglob("*.py"):
        ast.parse(py_file.read_text())


def test_full_manifest_no_errors(tmp_path):
    _, errors = run(tmp_path, FULL)
    assert not errors


def test_full_manifest_valid_python(tmp_path):
    out, _ = run(tmp_path, FULL)
    for py_file in out.rglob("*.py"):
        ast.parse(py_file.read_text())


def test_domain_model_fields(tmp_path):
    out, _ = run(tmp_path, SIMPLE)
    content = (out / "product/domain/models.py").read_text()
    assert "class Product:" in content
    assert "name: str" in content
    assert "price: float" in content
    assert "in_stock: bool = True" in content


def test_enum_imported_in_domain_model(tmp_path):
    out, _ = run(tmp_path, FULL)
    content = (out / "product/domain/models.py").read_text()
    assert "from .enums import Status" in content


def test_value_object_stored_as_json(tmp_path):
    out, _ = run(tmp_path, FULL)
    content = (out / "product/infrastructure/models.py").read_text()
    assert "JSON" in content


def test_auto_now_excluded_from_create_dto(tmp_path):
    out, _ = run(tmp_path, FULL)
    content = (out / "product/application/dtos.py").read_text()
    create_section = content[
        content.index("class ProductCreate") : content.index("class ProductUpdate")
    ]
    assert "created_at" not in create_section


def test_update_use_case_generated(tmp_path):
    out, _ = run(tmp_path, FULL)
    content = (out / "product/application/use_cases.py").read_text()
    assert "class UpdateProductUseCase" in content


def test_list_route_in_router(tmp_path):
    out, _ = run(tmp_path, FULL)
    content = (out / "product/presentation/router.py").read_text()
    assert "list_products" in content


def test_mapper_converts_enum(tmp_path):
    out, _ = run(tmp_path, FULL)
    content = (out / "product/infrastructure/repository.py").read_text()
    assert "Status(row.status)" in content
    assert "entity.status.value" in content


def test_mapper_converts_value_object(tmp_path):
    out, _ = run(tmp_path, FULL)
    content = (out / "product/infrastructure/repository.py").read_text()
    assert "Dimensions(**row.dimensions)" in content
    assert "dataclasses.asdict(entity.dimensions)" in content


def test_two_aggregates_independent(tmp_path):
    out, errors = run(
        tmp_path,
        """
from bendy import Aggregate

class User(Aggregate):
    email: str

class Order(Aggregate):
    total: float
""",
    )
    assert not errors
    assert (out / "user/domain/models.py").exists()
    assert (out / "order/domain/models.py").exists()
