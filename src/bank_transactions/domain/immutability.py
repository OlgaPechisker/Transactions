"""Small helpers for frozen dataclasses that normalise their collection fields."""

from collections.abc import Callable
from typing import Any


def freeze_field(instance: object, name: str, factory: Callable[[Any], Any]) -> None:
    object.__setattr__(instance, name, factory(getattr(instance, name)))
