"""Security policy helpers for tenant context."""

from __future__ import annotations

import functools
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, cast

from foundry_lite.domain.context import RequestContext

NO_TENANT_CONTEXT = "__foundry_lite_no_tenant_context__"

_CURRENT_TENANT_ID: ContextVar[str | None] = ContextVar("foundry_lite_current_tenant_id", default=None)
_TENANT_CONTEXT_MARKER = "__foundry_lite_tenant_context_bound__"


def current_tenant_id() -> str | None:
    return _CURRENT_TENANT_ID.get()


def set_current_tenant_id(tenant_id: str) -> Token[str | None]:
    return _CURRENT_TENANT_ID.set(_required_tenant_id(tenant_id))


def clear_current_tenant_id() -> Token[str | None]:
    return _CURRENT_TENANT_ID.set(None)


def reset_tenant_context(token: Token[str | None]) -> None:
    _CURRENT_TENANT_ID.reset(token)


@contextmanager
def tenant_context(tenant_id: str) -> Generator[None]:
    token = set_current_tenant_id(tenant_id)
    try:
        yield
    finally:
        reset_tenant_context(token)


def bind_tenant_context_public_methods[T](cls: type[T], *, include_inherited: bool = True) -> type[T]:
    classes = reversed(cls.mro()) if include_inherited else (cls,)
    for base in classes:
        _bind_class_dict(cls, base.__dict__)
    return cls


def _bind_class_dict[T](target: type[T], values: Mapping[str, Any]) -> None:
    for name, value in list(values.items()):
        if name.startswith("_") or not callable(value):
            continue
        if isinstance(value, staticmethod | classmethod):
            continue
        setattr(target, name, _tenant_context_operation(value))


def _tenant_context_operation[F: Callable[..., Any]](func: F) -> F:
    if getattr(func, _TENANT_CONTEXT_MARKER, False):
        return func

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        ctx = _ctx_from_call(args, kwargs)
        if ctx is None:
            return func(*args, **kwargs)
        with tenant_context(ctx.tenant_id):
            return func(*args, **kwargs)

    setattr(wrapper, _TENANT_CONTEXT_MARKER, True)
    return cast(F, wrapper)


def _ctx_from_call(args: tuple[Any, ...], kwargs: dict[str, Any]) -> RequestContext | None:
    ctx = kwargs.get("ctx")
    if isinstance(ctx, RequestContext):
        return ctx
    for value in args:
        if isinstance(value, RequestContext):
            return value
    return None


def _required_tenant_id(tenant_id: str) -> str:
    value = tenant_id.strip()
    if not value:
        raise ValueError("tenant_id cannot be blank")
    return value
