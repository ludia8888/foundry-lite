"""Local filesystem implementation of the ontology definition reader port."""

from __future__ import annotations

import hashlib
from pathlib import Path

from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailure,
    AdapterFailureContract,
    AdapterFailureKind,
    AdapterFailureMode,
)
from foundry_lite.application.ports.ontology_definition_reader import (
    OntologyDefinitionContent,
    OntologyDefinitionRead,
)


class LocalOntologyDefinitionReader:
    """Load ontology definitions from paths owned by the local runtime."""

    profile_name = "local-ontology-definition-reader"

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    operation="read_definition",
                    kind="not_found",
                    is_retryable=False,
                    operator_message="Ontology definition is missing; verify the configured location.",
                ),
                AdapterFailureMode(
                    operation="read_definition",
                    kind="validation",
                    is_retryable=False,
                    operator_message="Ontology definition is not valid UTF-8; provide a valid YAML document.",
                ),
                AdapterFailureMode(
                    operation="read_definition",
                    kind="unavailable",
                    is_retryable=True,
                    operator_message="Ontology definition storage is unavailable; retry the same request.",
                ),
            ),
        )

    def read_definition(self, request: OntologyDefinitionRead) -> OntologyDefinitionContent:
        data = self._read_bytes(request.location)
        try:
            yaml_text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise self._error(
                "validation",
                False,
                "Ontology definition is not valid UTF-8; provide a valid YAML document.",
            ) from exc
        return OntologyDefinitionContent(
            yaml_text=yaml_text,
            content_hash=f"sha256:{hashlib.sha256(data).hexdigest()}",
            byte_size=len(data),
        )

    def _read_bytes(self, location: str) -> bytes:
        try:
            return Path(location).read_bytes()
        except FileNotFoundError as exc:
            raise self._error(
                "not_found",
                False,
                "Ontology definition is missing; verify the configured location.",
            ) from exc
        except OSError as exc:
            raise self._error(
                "unavailable",
                True,
                "Ontology definition storage is unavailable; retry the same request.",
            ) from exc

    def _error(self, kind: AdapterFailureKind, is_retryable: bool, message: str) -> AdapterError:
        return AdapterError(
            AdapterFailure(
                adapter_profile=self.profile_name,
                operation="read_definition",
                kind=kind,
                is_retryable=is_retryable,
                operator_message=message,
            )
        )
