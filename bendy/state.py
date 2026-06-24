from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
BENDYKIT_VERSION = "0.1.0"


@dataclass
class FileState:
    template: str
    template_version: int
    top_level_ids: list[str]
    per_class_ids: dict[str, list[str]]


@dataclass
class BendyState:
    schema_version: int = SCHEMA_VERSION
    bendykit_version: str = BENDYKIT_VERSION
    generated_at: str = ""
    aggregates: dict[str, dict[str, FileState]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> BendyState:
        if not path.exists():
            return cls()
        try:
            raw: dict[str, Any] = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return cls()

        aggregates: dict[str, dict[str, FileState]] = {}
        for agg_name, files in raw.get("aggregates", {}).items():
            aggregates[agg_name] = {}
            for rel_path, fs in files.items():
                aggregates[agg_name][rel_path] = FileState(
                    template=fs.get("template", ""),
                    template_version=fs.get("template_version", 0),
                    top_level_ids=fs.get("top_level_ids", []),
                    per_class_ids=fs.get("per_class_ids", {}),
                )

        return cls(
            schema_version=raw.get("schema_version", SCHEMA_VERSION),
            bendykit_version=raw.get("bendykit_version", ""),
            generated_at=raw.get("generated_at", ""),
            aggregates=aggregates,
        )

    def save(self, path: Path) -> None:
        self.generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        data: dict[str, Any] = {
            "schema_version": self.schema_version,
            "bendykit_version": self.bendykit_version,
            "generated_at": self.generated_at,
            "aggregates": {
                agg_name: {
                    rel_path: {
                        "template": fs.template,
                        "template_version": fs.template_version,
                        "top_level_ids": sorted(fs.top_level_ids),
                        "per_class_ids": {
                            k: sorted(v) for k, v in sorted(fs.per_class_ids.items())
                        },
                    }
                    for rel_path, fs in sorted(files.items())
                }
                for agg_name, files in sorted(self.aggregates.items())
            },
        }
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    def get_file(self, agg_name: str, rel_path: str) -> FileState | None:
        return self.aggregates.get(agg_name, {}).get(rel_path)

    def set_file(self, agg_name: str, rel_path: str, fs: FileState) -> None:
        self.aggregates.setdefault(agg_name, {})[rel_path] = fs
