"""JSON payload projection for Pipeline Builder node descriptors."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from foundry_lite.application.services.pipeline_node_execution_policy import (
    PipelineNodeExecutionPolicy,
)

JsonObject = dict[str, object]


class _EnumValue(Protocol):
    @property
    def value(self) -> str: ...


class _InputPortDescriptor(Protocol):
    @property
    def port_id(self) -> str: ...

    @property
    def accepted_artifact_kinds(self) -> Sequence[_EnumValue]: ...

    @property
    def cardinality(self) -> _EnumValue: ...

    @property
    def is_required(self) -> bool: ...


class _OutputPortDescriptor(Protocol):
    @property
    def port_id(self) -> str: ...

    @property
    def artifact_kind(self) -> _EnumValue: ...


class _ConfigFieldDescriptor(Protocol):
    @property
    def field_name(self) -> str: ...

    @property
    def value_kind(self) -> _EnumValue: ...

    @property
    def is_required(self) -> bool: ...

    @property
    def allowed_values(self) -> Sequence[str]: ...


class _NodeDescriptor(Protocol):
    @property
    def descriptor_id(self) -> str: ...

    @property
    def spec_version(self) -> int: ...

    @property
    def node_kind(self) -> _EnumValue: ...

    @property
    def availability(self) -> _EnumValue: ...

    @property
    def runtime_capability(self) -> str: ...

    @property
    def input_ports(self) -> Sequence[_InputPortDescriptor]: ...

    @property
    def output_ports(self) -> Sequence[_OutputPortDescriptor]: ...

    @property
    def config_fields(self) -> Sequence[_ConfigFieldDescriptor]: ...

    @property
    def execution_policy(self) -> PipelineNodeExecutionPolicy: ...


def pipeline_node_descriptor_payload(descriptor: _NodeDescriptor) -> JsonObject:
    policy = descriptor.execution_policy
    return {
        "descriptorId": descriptor.descriptor_id,
        "specVersion": descriptor.spec_version,
        "kind": descriptor.node_kind.value,
        "availability": descriptor.availability.value,
        "runtimeCapability": descriptor.runtime_capability,
        "inputPorts": [_input_port_payload(port) for port in descriptor.input_ports],
        "outputPorts": [_output_port_payload(port) for port in descriptor.output_ports],
        "configFields": [_config_field_payload(field) for field in descriptor.config_fields],
        "executionPolicy": {
            "maximumAttempts": policy.maximum_attempts,
            "initialBackoffSeconds": policy.initial_backoff_seconds,
            "maximumBackoffSeconds": policy.maximum_backoff_seconds,
            "timeoutSeconds": policy.timeout_seconds,
            "requiresStableIdempotency": policy.requires_stable_idempotency,
        },
    }


def _input_port_payload(port: _InputPortDescriptor) -> JsonObject:
    return {
        "portId": port.port_id,
        "acceptedArtifactKinds": [kind.value for kind in port.accepted_artifact_kinds],
        "cardinality": port.cardinality.value,
        "required": port.is_required,
    }


def _output_port_payload(port: _OutputPortDescriptor) -> JsonObject:
    return {"portId": port.port_id, "artifactKind": port.artifact_kind.value}


def _config_field_payload(field: _ConfigFieldDescriptor) -> JsonObject:
    return {
        "fieldName": field.field_name,
        "valueKind": field.value_kind.value,
        "required": field.is_required,
        "allowedValues": list(field.allowed_values),
    }
