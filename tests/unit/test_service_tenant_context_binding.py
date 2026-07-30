"""Every tenant-scoped service operation must run inside a bound tenant context.

``CoreService.__init_subclass__`` wraps public service methods so the tenant id
from the caller's ``RequestContext`` is bound to a contextvar for the duration of
the call. ``install_postgres_rls_tenant_context`` reads that contextvar on every
transaction ``begin`` and issues ``set_config`` for the RLS policies; when nothing
is bound it falls back to ``NO_TENANT_CONTEXT``, which matches no row.

So an unbound operation is not a cosmetic gap. On PostgreSQL every read it makes
returns zero rows and every write fails its ``WITH CHECK`` policy. This scan
guards the whole service graph: it previously missed the operations contributed
by ``RuntimeObservabilityMixin``, because the binding hook only walked each
class's own ``__dict__`` and a plain mixin never runs that hook.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import foundry_lite.application.services as services_package
from foundry_lite.application.services.base import CoreService
from foundry_lite.security.tenant_context import _TENANT_CONTEXT_MARKER


def _import_all_service_modules() -> None:
    for module in pkgutil.walk_packages(services_package.__path__, f"{services_package.__name__}."):
        try:
            importlib.import_module(module.name)
        except Exception:  # noqa: BLE001 - optional adapters may not import in every environment
            continue


def _all_service_classes() -> set[type]:
    def descendants(cls: type) -> set[type]:
        found: set[type] = set()
        for subclass in cls.__subclasses__():
            found.add(subclass)
            found |= descendants(subclass)
        return found

    return descendants(CoreService)


def test_every_ctx_taking_service_operation_binds_tenant_context() -> None:
    _import_all_service_modules()

    unbound: list[str] = []
    for service in _all_service_classes():
        for name in dir(service):
            if name.startswith("_"):
                continue
            try:
                attribute = inspect.getattr_static(service, name)
            except AttributeError:
                continue
            if not callable(attribute) or isinstance(attribute, staticmethod | classmethod):
                continue
            try:
                signature = inspect.signature(attribute)
            except (TypeError, ValueError):
                continue
            # Only ctx-taking operations reach the database on a caller's behalf.
            if "ctx" not in signature.parameters:
                continue
            if not getattr(attribute, _TENANT_CONTEXT_MARKER, False):
                unbound.append(f"{service.__name__}.{name}")

    assert not unbound, (
        "service operations accept a RequestContext but never bind its tenant id; "
        f"under PostgreSQL RLS these read zero rows and fail writes: {sorted(unbound)}"
    )
