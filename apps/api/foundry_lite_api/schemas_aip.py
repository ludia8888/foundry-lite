"""AIP and agent request schemas."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from foundry_lite_api.schema_types import JsonObject


class AipBuilderContextSourceRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source_id: str = Field(alias="sourceId")
    kind: str
    security_partition: str = Field(alias="securityPartition")
    selected_properties: list[str] = Field(default_factory=list, alias="selectedProperties")
    token_budget: int = Field(default=800, alias="tokenBudget")


class AipBuilderToolSpecRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    tool_id: str = Field(alias="toolId")
    version: str
    description: str = ""
    input_schema: JsonObject = Field(default_factory=dict, alias="inputSchema")
    output_schema: JsonObject = Field(default_factory=dict, alias="outputSchema")
    effect: str = "READ"
    required_permission: str = Field(default="object:read", alias="requiredPermission")
    confirmation_policy: str = Field(default="NONE", alias="confirmationPolicy")
    object_type_allowlist: list[str] = Field(default_factory=list, alias="objectTypeAllowlist")
    property_allowlist: list[str] = Field(default_factory=list, alias="propertyAllowlist")
    timeout_seconds: int = Field(default=30, alias="timeoutSeconds")
    max_result_items: int = Field(default=50, alias="maxResultItems")
    result_classification: str = Field(default="public", alias="resultClassification")
    status: str = "published"


class AipBuilderLogicBlockRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    block_id: str = Field(alias="blockId")
    kind: str
    inputs: JsonObject = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list, alias="dependsOn")


class AipBuilderValidateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    agent_version_id: str = Field(alias="agentVersionId")
    release_channel: str = Field(alias="releaseChannel")
    model_alias_version: str = Field(alias="modelAliasVersion")
    prompt_version_id: str = Field(alias="promptVersionId")
    context_sources: list[AipBuilderContextSourceRequest] = Field(alias="contextSources")
    tool_manifest: list[AipBuilderToolSpecRequest] = Field(alias="toolManifest")
    logic_blocks: list[AipBuilderLogicBlockRequest] = Field(alias="logicBlocks", max_length=500)
    eval_axes: list[str] = Field(alias="evalAxes")
    agent_allowed_actions: list[str] = Field(default_factory=list, alias="agentAllowedActions")
    max_logic_blocks: int = Field(default=25, ge=1, le=500, alias="maxLogicBlocks")


class AipBuilderRunRequest(AipBuilderValidateRequest):
    logic_run_id: str = Field(alias="logicRunId")
    ai_run_id: str | None = Field(default=None, alias="aiRunId")
    session_id: str | None = Field(default=None, alias="sessionId")
    input_json: JsonObject = Field(default_factory=dict, alias="inputJson")
    user_message: str = Field(default="", alias="userMessage")
    agent_allowed_tools: list[str] = Field(default_factory=list, alias="agentAllowedTools")
    model_allowed_classifications: list[str] = Field(
        default_factory=lambda: ["public", "internal"],
        alias="modelAllowedClassifications",
    )
    policy_version: str = Field(default="policy-v1", alias="policyVersion")


class AipAgentRunRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    agent_run_id: str = Field(default="agent-run-default", alias="agentRunId")
    agent_version_id: str = Field(alias="agentVersionId")
    model_alias: str = Field(default="default-completion", alias="modelAlias")
    prompt_version_id: str = Field(alias="promptVersionId")
    user_message: str = Field(alias="userMessage")
    agent_instruction: str = Field(
        default="Answer the operator using cited context.",
        alias="agentInstruction",
    )
    security_partition: str = Field(alias="securityPartition")
    allowed_security_partitions: list[str] = Field(alias="allowedSecurityPartitions")
    state_json: JsonObject = Field(default_factory=dict, alias="stateJson")
    output_schema: JsonObject | None = Field(default=None, alias="outputSchema")
    ai_run_id: str | None = Field(default=None, alias="aiRunId")
    session_id: str | None = Field(default=None, alias="sessionId")
    ontology_version_id: str = Field(default="active-ontology", alias="ontologyVersionId")
    data_classification: str = Field(default="internal", alias="dataClassification")
    model_allowed_classifications: list[str] | None = Field(default=None, alias="modelAllowedClassifications")
    region_requirement: str | None = Field(default=None, alias="regionRequirement")
    max_context_items: int = Field(default=4, alias="maxContextItems")
    max_context_tokens: int = Field(default=1200, alias="maxContextTokens")
    max_model_calls: int = Field(default=1, alias="maxModelCalls")
    max_loop_iterations: int = Field(default=1, alias="maxLoopIterations")
    max_tool_calls: int = Field(default=0, alias="maxToolCalls")
    max_tool_output_bytes: int = Field(default=4096, alias="maxToolOutputBytes")
    max_output_tokens: int = Field(default=512, alias="maxOutputTokens")
    policy_version: str = Field(default="policy-v1", alias="policyVersion")
    tool_manifest: list[AipBuilderToolSpecRequest] = Field(default_factory=list, alias="toolManifest")
    agent_allowed_tools: list[str] = Field(default_factory=list, alias="agentAllowedTools")
    agent_allowed_actions: list[str] = Field(default_factory=list, alias="agentAllowedActions")


class AipFdeRunRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_message: str = Field(alias="userMessage", min_length=1, max_length=20000)
    workspace_ref: str | None = Field(default=None, alias="workspaceRef", min_length=1, max_length=512)
    branch_id: str | None = Field(default=None, alias="branchId", min_length=1, max_length=255)
    mode: str = Field(default="ontology_editing", max_length=64)
    capabilities: list[str] = Field(default_factory=list, max_length=20)
    approved_tool_ids: list[str] = Field(default_factory=list, alias="approvedToolIds", max_length=20)
    attached_context_refs: list[str] = Field(default_factory=list, alias="attachedContextRefs", max_length=20)
    model_alias: str = Field(default="default-completion", alias="modelAlias", max_length=255)
    session_id: str | None = Field(default=None, alias="sessionId", max_length=255)
    agent_run_id: str | None = Field(default=None, alias="agentRunId", max_length=255)
    tool_discovery: str = Field(default="eager", alias="toolDiscovery", pattern="^(eager|lazy)$")
    max_context_items: int = Field(default=6, ge=1, le=20, alias="maxContextItems")
    max_context_tokens: int = Field(default=2400, ge=128, le=32000, alias="maxContextTokens")
    max_tool_calls: int = Field(default=4, ge=1, le=8, alias="maxToolCalls")
    max_output_tokens: int = Field(default=512, ge=64, le=16000, alias="maxOutputTokens")


class AipPilotFieldRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    api_name: str | None = Field(default=None, alias="apiName", pattern="^[A-Za-z][A-Za-z0-9]{0,63}$")
    type: str = Field(default="string", pattern="^(string|integer|float|boolean|date|timestamp)$")
    required: bool = False
    description: str = Field(default="", max_length=300)


class AipPilotRecordRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    api_name: str | None = Field(default=None, alias="apiName", pattern="^[A-Za-z][A-Za-z0-9]{0,63}$")
    description: str = Field(default="", max_length=500)
    fields: list[AipPilotFieldRequest] = Field(default_factory=list, max_length=20)


class AipPilotActionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    api_name: str | None = Field(default=None, alias="apiName", pattern="^[A-Za-z][A-Za-z0-9]{0,63}$")
    description: str = Field(default="", max_length=500)
    from_states: list[str] = Field(default_factory=list, alias="fromStates", max_length=8)
    to_state: str = Field(alias="toState", min_length=1, max_length=120)
    required_information: list[str] = Field(default_factory=list, alias="requiredInformation", max_length=12)
    allowed_actors: list[str] = Field(default_factory=list, alias="allowedActors", max_length=12)
    requires_approval: bool = Field(default=False, alias="requiresApproval")


class AipPilotPolicyConditionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    property_api_name: str = Field(alias="propertyApiName", pattern="^[A-Za-z][A-Za-z0-9]{0,63}$")
    operator: Literal[
        "eq", "neq", "in", "notIn", "lt", "lte", "gt", "gte", "contains", "startsWith", "matches", "exists"
    ]
    value: object | None = None

    @model_validator(mode="after")
    def validate_value_presence(self) -> Self:
        has_value = "value" in self.model_fields_set
        if self.operator == "exists" and has_value:
            raise ValueError("exists policy conditions must omit value")
        if self.operator != "exists" and not has_value:
            raise ValueError(f"{self.operator} policy conditions require value")
        return self


class AipPilotPolicyRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    statement: str = Field(min_length=1, max_length=1000)
    enforcement: str = Field(default="blocking", pattern="^(blocking|warning|manual_review)$")
    evidence: str = Field(default="", max_length=500)
    applies_to_actions: list[str] = Field(default_factory=list, alias="appliesToActions", max_length=20)
    conditions: list[AipPilotPolicyConditionRequest] = Field(default_factory=list, max_length=12)


class AipPilotDomainBriefRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    actors: list[str] = Field(default_factory=list, max_length=12)
    records: list[AipPilotRecordRequest] = Field(default_factory=list, max_length=8)
    lifecycle_states: list[str] = Field(default_factory=list, alias="lifecycleStates", max_length=16)
    actions: list[AipPilotActionRequest] = Field(default_factory=list, max_length=20)
    policies: list[AipPilotPolicyRequest] = Field(default_factory=list, max_length=20)
    evidence: list[str] = Field(default_factory=list, max_length=20)
    integrations: list[str] = Field(default_factory=list, max_length=20)
    success_measures: list[str] = Field(default_factory=list, alias="successMeasures", max_length=20)


class AipPilotPlanRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    application_name: str = Field(alias="applicationName", min_length=1, max_length=255)
    domain_description: str = Field(alias="domainDescription", min_length=1, max_length=10000)
    domain_brief: AipPilotDomainBriefRequest = Field(alias="domainBrief")


class AipPilotGenerateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    plan: JsonObject


class AipCitationNavigationResolveRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    navigation_ref: str = Field(alias="navigationRef", min_length=1, max_length=32768)


class AipEvalCaseRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    case_api_name: str = Field(alias="caseApiName")
    axis: str
    input_json: JsonObject = Field(default_factory=dict, alias="inputJson")
    expected_json: JsonObject = Field(default_factory=dict, alias="expectedJson")
    actual_json: JsonObject = Field(default_factory=dict, alias="actualJson")
    rubric_json: JsonObject = Field(default_factory=dict, alias="rubricJson")
    tags: list[str] = Field(default_factory=list)
    sample_index: int = Field(default=1, alias="sampleIndex")
    evaluator: str = "exact_subset_v1"
    weight: float = 1.0


class AipEvalRunRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    eval_run_id: str = Field(alias="evalRunId")
    suite_api_name: str = Field(alias="suiteApiName")
    suite_version: str = Field(alias="suiteVersion")
    suite_description: str = Field(default="", alias="suiteDescription")
    agent_version_id: str = Field(alias="agentVersionId")
    candidate_release_channel: str = Field(alias="candidateReleaseChannel")
    cases: list[AipEvalCaseRequest] = Field(max_length=500)
    min_score: float = Field(default=1.0, alias="minScore")
    required_axes: list[str] = Field(default_factory=list, alias="requiredAxes")


class AipReleasePromotionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    agent_version_id: str = Field(alias="agentVersionId")
    target_release_channel: str = Field(alias="targetReleaseChannel")
    eval_run_id: str = Field(alias="evalRunId")
    policy_version: str = Field(default="release-policy-v1", alias="policyVersion")
