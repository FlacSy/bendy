from typing import Any

from .types import MISSING


class Field:
    def __init__(
        self,
        default: Any = MISSING,
        *,
        auto_now: bool = False,
        unique: bool = False,
        index: bool = False,
        max_length: int | None = None,
    ) -> None:
        if auto_now and default is not MISSING:
            raise ValueError("cannot set both default and auto_now")
        self.default = default
        self.auto_now = auto_now
        self.unique = unique
        self.index = index
        self.max_length = max_length

    @property
    def has_default(self) -> bool:
        return self.default is not MISSING
