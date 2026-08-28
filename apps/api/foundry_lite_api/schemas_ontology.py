"""Ontology and function request schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from foundry_lite_api.schema_types import JsonObject


class FunctionExecuteRequest(BaseModel):
    inputs: JsonObject = Field(default_factory=dict)


class OntologyValidateRequest(BaseModel):
    yaml_text: str = Field(alias="yaml")


class OntologyApplyRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    yaml_text: str = Field(alias="yamlText")


class OntologyRollbackRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    version_number: int = Field(alias="versionNumber", ge=1)


class OntologyProposalSubmitRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    yaml_text: str = Field(alias="yamlText")
    title: str
    description: str | None = None


class OntologyProposalUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    yaml_text: str = Field(alias="yamlText")
    expected_fingerprint: str = Field(alias="expectedFingerprint")
    title: str | None = None
    description: str | None = None


class OntologyProposalAssignRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    reviewer_user_id: str = Field(alias="reviewerUserId")


class OntologyProposalDecisionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    decision: str
    expected_fingerprint: str = Field(alias="expectedFingerprint")
    comment: str | None = None


class OntologyProposalExecuteRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    expected_fingerprint: str = Field(alias="expectedFingerprint")


class OntologyProposalWithdrawRequest(BaseModel):
    reason: str | None = None


class OntologyBranchCreateRequest(BaseModel):
    name: str


class OntologyBranchUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    yaml_text: str = Field(alias="yamlText")
    expected_fingerprint: str = Field(alias="expectedFingerprint")


class OntologyBranchActionTypeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    definition: JsonObject
    expected_fingerprint: str = Field(alias="expectedFingerprint")


class OntologyBranchActionTypeDeleteRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    expected_fingerprint: str = Field(alias="expectedFingerprint")


class OntologyBranchRebaseResolution(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kind: str
    api_name: str = Field(alias="apiName")
    use: str


class OntologyBranchRebaseRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    expected_fingerprint: str = Field(alias="expectedFingerprint")
    resolutions: list[OntologyBranchRebaseResolution] = Field(default_factory=list)


class OntologyBranchProposeRequest(BaseModel):
    title: str
    description: str | None = None
