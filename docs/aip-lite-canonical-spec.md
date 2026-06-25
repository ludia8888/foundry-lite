# AIP-lite Canonical Spec (in-repo encoding of `Foundry-lite_AIP_Architecture_Report.pdf`)

> **Source of truth.** The authoritative design is `docs/Foundry-lite_AIP_Architecture_Report.pdf`
> (49 pages, dated 2026-06-25, v1.0). This markdown is a derived, exact-name checklist so every
> AIP-lite section can be built without re-extracting the PDF each time. **When this note and the
> PDF disagree, the PDF wins** — fix this note. Section refs (§N / pNN) point into the PDF.
>
> **Process rule (standing).** Every AIP-lite section: read this note + the relevant PDF section
> **first**, then do Palantir official research (the PDF's P-xx sources), de-risk, build, verify,
> PR (Root Cause/Impact/Regression Test), CI 8-check green, merge. See `feedback_palantir-research-first`.

## Final design thesis (§1.1, §17.2)

LLM = **untrusted planner**, not a decision-maker or data-access layer. All data reads go through
existing permission-applying services; all operational writes go through the existing `ActionService`;
high-risk changes go through a normalized **proposal + human review**. Goal end state:
**Ontology-grounded, permission-aware, evidence-backed, action-capable AI operating layer** — an
extension of the existing Operational Object System, NOT a bolted-on chatbot.

## Layering (§3.1, fig.1) & recommended adoption order (§1.4, §17.2)

Foundry Operational Plane (existing core = truth) · AIP Control Plane (immutable definitions) ·
AIP Runtime Plane (per-turn execution + AI ledger) · Apollo Plane (release channels).

Order: **search security → Model Gateway/Alias → AI Run&Event Ledger → Context Compiler/Retrieval
Orchestrator → read-only Tool Broker → Citation → Action Proposal → Approval-to-Action bridge →
Evals/Release → Logic Runtime → Visual Builder.**

## Ten design principles (§7.1)

1. Ontology-first context+tools. 2. LLM-untrusted (no exec without validation). 3. Authoritative
   re-read (index hit ≠ truth; re-read DB). 4. Identity propagation (tools run as the end user/tenant).
2. Immutable definitions (model alias / prompt / agent / tool / eval pinned by version). 6. Evidence
   by default (every claim + action proposal carries a source ref). 7. Bounded autonomy (model/tool/
   loop/time/token/cost budget enforced). 8. Separation of truth and projection (vector/search cache =
   rebuildable). 9. Human gate for risk (irreversible/external/financial writes need review). 10.
   Release before automation (no production autonomous agent without eval + promotion).

## Effective permission = intersection (§9.1)

`User ∩ AgentDefinitionAllowlist ∩ Tool/ActionPolicy ∩ ResourceSecurity(tenant·classification·purpose)
∩ ModelEgressPolicy(provider·region·retention) ∩ Environment/ReleasePolicy`. User having an Action
permission but the agent definition lacking that Action ⇒ cannot run, and vice-versa.

---

## Data model — exact tables/columns (§10)

### Control Plane (§10.1)

- **ai_model_providers**: `id, tenant_scope, provider_type, profile_name, region, secret_ref,
retention_policy, training_policy, status, created_at`
- **ai_models**: `id, provider_id, provider_model_id, revision, lifecycle, capabilities_json,
context_limit, output_limit, pricing_json, allowed_classifications, created_at`
- **ai_model_aliases**: `id, alias, environment, model_id, version, status, eval_run_id,
effective_at, retired_at` ← **`version` IS the pinned model revision**; `status` ∈
  draft/enabled/deprecated/disabled (§8.2).
- **ai_prompt_versions**: `id, api_name, version, template, template_hash, input_schema, created_by,
status, created_at`
- **ai_agent_versions**: `id, agent_api_name, version, status, model_alias_version, prompt_version_id,
tool_manifest_hash, context_policy, output_schema, budget, risk_policy, ontology_compatibility,
release_channel, created_at`
- **ai_eval_suites**: `id, tenant_id, suite_api_name, version, description, axes_json, status,
created_at`
- **ai_eval_cases**: `id, tenant_id, suite_id, case_api_name, axis, input_json, expected_json,
rubric_json, tags_json, created_at`
- **ai_eval_runs**: `id, tenant_id, suite_id, agent_version_id, candidate_release_channel, status,
min_score, passed, summary_json, started_at, completed_at`
- **ai_eval_results**: `id, tenant_id, eval_run_id, case_id, sample_index, axis, score, passed,
evaluator, input_hash, expected_hash, actual_hash, result_json, created_at`
- **ai_agent_releases** (local first release-guard ledger): `id, tenant_id, agent_version_id,
release_channel, eval_run_id, status, policy_version, promoted_by, promoted_at, created_at`

### Runtime Plane (§10.2)

- **ai_sessions**: `id, tenant_id, agent_version_id, actor_user_id, status, created_at, last_activity_at`
- **ai_session_state_versions**: `id, tenant_id, session_id, version, state_json, state_hash,
created_by_run_id, created_at`
- **ai_messages**: `id, tenant_id, session_id, role, client_message_id, content_ref, content_hash,
created_at` + `UNIQUE(tenant_id, session_id, client_message_id)` (idempotency).
- **ai_execution_runs**: `id, tenant_id, session_id, agent_version_id, actor_user_id, request_id,
trace_id, status, ontology_version_id, model_alias_version, resolved_model_id, resolved_model_revision,
prompt_version_id, compiled_prompt_hash, tool_manifest_hash, context_manifest_hash, state_snapshot_hash,
policy_snapshot_hash, budget_json, usage_json, error_json, started_at, completed_at`
- **ai_execution_events**: `id, tenant_id, ai_run_id, sequence, event_type, payload_ref, payload_hash,
redacted_preview, created_at` + `UNIQUE(ai_run_id, sequence)`
- **ai_model_calls**: `id, tenant_id, ai_run_id, attempt, provider, model_revision, request_hash,
response_hash, provider_request_id, input_tokens, output_tokens, latency_ms, status, error_json`
- **ai_context_items**: `id, tenant_id, ai_run_id, context_id, kind, source_resource_type,
source_resource_id, source_version, content_hash, retrieval_method, relevance_score,
security_partition, token_estimate, selected, omission_reason`
- **ai_tool_calls**: `id, tenant_id, ai_run_id, sequence, tool_id, tool_version, arguments_hash,
effect, authorization_decision, confirmation_policy, status, result_hash, linked_action_run_id,
started_at, completed_at, error_json`
- **ai_citations**: `id, tenant_id, ai_run_id, message_id, context_item_id, claim_span,
citation_order, rendered_ref`
- **ai_usage_ledger**: `id, tenant_id, ai_run_id, provider, model_revision, input_tokens,
output_tokens, estimated_cost, currency, recorded_at`

### Insight review extension (§10.3) — add to existing `insight_reviews`

`proposal_type, proposal_fingerprint, originating_ai_run_id, originating_tool_call_id, expires_at,
execution_status, approved_action_run_id, approval_policy_version`

### Existing runtime linkage (§10.5)

Add `ai`/`ai_execution` to `runtime_run_relations` run-type literal (or a generalized relation table).
Relations: AI run --used--> object version / content unit; --called--> model call; --proposed-->
insight review; insight review --approved_as--> action run; action run --produced--> object edit /
--emitted--> outbox event.

---

## Ports & packages — exact paths (§11)

### New ports `libs/foundry_lite/application/ports/` (§11.1)

`language_model.py, model_registry_repository.py, ai_definition_repository.py, ai_run_repository.py,
context_provider.py, tool_executor.py, usage_meter.py`

### New services `libs/foundry_lite/application/services/aip/` (§11.2)

`model_gateway.py, definition_service.py, session_service.py, state_service.py, context_compiler.py,
retrieval_orchestrator.py, tool_registry.py, tool_broker.py, agent_runtime.py, logic_runtime.py,
citation_service.py, approval_execution.py, eval_service.py`

### New infrastructure (§11.3)

`infrastructure/adapters/provider_compatible_language_model.py, fake_language_model.py` ·
`infrastructure/repositories/sqlalchemy_ai_definition_repository.py, sqlalchemy_ai_run_repository.py,
sqlalchemy_model_registry_repository.py`

### Service composition (§11.4)

```python
@dataclass(frozen=True)
class AipServices:
    model_gateway: ModelGatewayService
    definitions: AgentDefinitionService
    sessions: SessionService
    runtime: AgentRuntimeService
    logic: LogicRuntimeService
    evals: EvalService
```

Facade: `foundry.aip.agents / .sessions / .runs / .evals`.

### LanguageModelAdapter port (§8.1.2)

```python
class LanguageModelAdapter(Protocol):
    @property
    def profile_name(self) -> str: ...
    def complete(self, request: ModelRequest) -> ModelResponse: ...
    def stream(self, request: ModelRequest) -> Iterable[ModelEvent]: ...
    def failure_contract(self) -> AdapterFailureContract: ...
```

**ModelRequest** (§8.1.3): `model_alias, messages, tools, response_schema, temperature,
max_output_tokens, request_id, ai_run_id, data_classification, region_requirement, timeout_seconds`.
**ModelResponse** (§8.1.4): `provider, resolved_model_id, resolved_model_revision, content,
normalized_tool_calls, finish_reason, input_tokens, output_tokens, provider_request_id, latency_ms`.
**Retry** (§8.1.5): only transport timeout / 429 / temporary unavailable, retried under the same
request fingerprint + idempotency context; **tool-producing response must not be silently retried**;
**write-producing agent must not silently fall back to a different model alias.**

### Error taxonomy (§11.6) — extends `AdapterFailureContract`

- **model**: `timeout | rate_limited | unavailable | content_rejected | schema_invalid | egress_denied`
- **context**: `security_denied | budget_exceeded`
- **tool**: `not_allowed | schema_invalid | policy_denied | confirmation_required`
- **agent**: `budget_exceeded`
- **proposal**: `expired | fingerprint_mismatch | approval_object_version_conflict`

Every error payload carries `request_id, ai_run_id, retryability, operator_message, safe_details`.
(NB: model-level egress denial is **`egress_denied`**, NOT `policy_denied`; `policy_denied` is the
**tool**-level term.)

---

## Component contracts

- **Model Gateway (§8.1)**: alias → provider/model/revision resolve · request normalization ·
  native/prompted tool-calling capability negotiation · structured-output capability check · provider
  secret resolve (via SecretProvider) · egress policy check · timeout/rate-limit/retry · usage+latency
  record · provider error → typed failure.
- **Model Alias (§8.2)**: agent references the **alias**, never the provider-native model id. Alias
  record carries lifecycle, environment mapping, capabilities (streaming/native tools/parallel tools/
  JSON schema/vision), context/output token limits, provider region, allowed classifications,
  retention/training assurance, rate/cost budget, eval evidence.
- **Retrieval Orchestrator (§8.5)**: query → deterministic normalization → lexical candidates → dense
  candidates → rank fusion (BM25+dense cosine+**RRF**) → optional reranking (HyDE/query-aug/LLM-rerank
  introduced only after baseline eval) → authoritative DB re-read → security validation → version/hash
  validation → dedup/diversity → token-budget packing → `RetrievedContextItem[]`. Returns
  `RetrievedContextItem{context_id, kind∈object|document|function, text, source_ref, source_version,
content_hash, relevance_score, retrieval_method, security_partition, token_estimate}`.
- **Context Compiler (§8.6)**: fixed order = 1 platform safety policy, 2 published agent instruction,
  3 state schema+visible values, 4 tool definitions, 5 authoritative retrieved context, 6 citation
  mapping, 7 output schema, 8 user message. Emits `compiled_prompt_hash, context_manifest_hash,
tool_manifest_hash, state_snapshot_hash, policy_snapshot_hash`. Retrieved docs are **untrusted data**,
  fenced by explicit delimiter+policy so embedded "ignore previous instructions" is never promoted to
  system instruction.
- **Citation Service (§8.7)**: model is given **opaque context IDs**, never source URLs; service maps
  `ctx_id → media item version / page / content unit / hash` and verifies the context ID is in this
  run's manifest + caller may read source + version/hash still match + span relevant. Returns display
  payload + signed navigation ref.
- **Tool Registry / Tool Broker (§8.8)**: `ToolSpec{tool_id, version, input_schema, output_schema,
effect∈READ|PROPOSE_WRITE|WRITE, required_permission, confirmation_policy∈NONE|USER|HUMAN_REVIEW,
object_type_allowlist, property_allowlist, timeout_seconds, max_result_items}`. Broker check order:
  agent allowlist → published tool version → input JSON schema → user permission → object/property scope
  → masked property rejection → model egress compatibility → budget/timeout/result limit →
  confirmation/review requirement → idempotency+request fingerprint → output masking before returning to
  model. Initial allow: `ontology.get_object/query_objects/get_links/search_objects, content.search,
state.update, action.propose`. Initial deny: `generic_sql, arbitrary_http_request, shell_execute,
python_eval, generic_repository_write`.
- **Agent Runtime (§8.9)**: bounded loop `RECEIVED → RESOLVING_DEFINITION → RETRIEVING_CONTEXT →
MODEL_RUNNING → TOOL_PENDING → TOOL_RUNNING → WAITING_CONFIRMATION → WAITING_HUMAN_REVIEW →
MODEL_RUNNING → SUCCEEDED`; terminal `FAILED/CANCELLED/BUDGET_EXCEEDED/POLICY_DENIED`. Budget: max
  model calls / tool calls / loop iterations / input+output tokens / wall-clock / estimated cost /
  context items / tool output bytes.
- **Action Proposal + Approval Execution (§8.10, §12.2)**: `ActionProposal{proposal_id, action_type,
target_object_type, target_object_id, expected_object_version, parameters, evidence_refs,
originating_ai_run_id, proposal_fingerprint, policy_version, expires_at}`. Fingerprint over
  action type + target type/id + expected object version + canonical params + evidence refs + agent
  version + policy version. On approval re-check: fingerprint unchanged, not expired, reviewer
  permission, action enabled, current object version, source evidence access, restore/write traffic
  gate, policy still compatible. **`ApprovalExecutionService` performs execution** (InsightReviewService
  does not call ActionService directly — separate approval event from execution ledger). Action
  idempotency key = proposal fingerprint.
- **Logic Runtime (§8.11)**: blocks Input/RetrieveObject/QueryObjects/TraverseLinks/RetrieveContent/
  CallLLM/CallFunction/Condition/Map/Reduce/UpdateState/CreateActionProposal/ApplyAction/HumanApproval/
  Output. Temporal only for long-running (human approval, batch, external side-effect compensation,
  crash-safe pause/resume); interactive chat = short in-process bounded loop. WorkflowAdapter additions:
  `start_workflow_async, signal_workflow, cancel_workflow, query_workflow, workflow_run`.
- **Evals / Release (§8.12, §15)**: axes Retrieval/Answer/Citation/Tool/Action/Security/Operations.
  Release channel `draft → dev → canary → stable → sunset`. Write-producing agent promoted to stable
  only after deterministic security/action gate + repeated-run variance.
- **Visual Builder (§14.6)**: Agent Studio / Context source editor / Tool manifest editor / Logic DAG
  canvas / Eval dashboard / AI run debugger is implemented after backend contracts are stable. The first
  Foundry-lite slice is a read-only Builder preflight that validates pinned agent/model/prompt/context/
  tool/Logic/eval drafts before runtime execution or release promotion.

---

## Security / egress / logging (§9)

- **Identity propagation (§9.2)**: model provider credential = system credential, but tool execution
  identity = end user; service account only for explicitly-named system/eval workers; approval reviewer
  separable from proposal creator.
- **Model egress policy (§9.3)**: per classification → allowed provider/region. `public` = approved
  external providers; `internal` = contracted zero-retention only; `confidential` = specific region/
  provider or self-hosted; `restricted/PII` = raw value forbidden or tokenized. Egress decision logs
  `provider, model_revision, region, classification_set, redaction/tokenization_applied, policy_version,
decision_reason`.
- **Retrieval security (§9.4)**: candidate **pre-filter by principal token before ranking** (unauthorized
  candidate must not affect score or top-k — already enforced for content via P0a), authoritative re-read
  post-validation, prompt property allowlist, per-classification context cap, index-generation security
  policy version pin, final egress redaction before provider.
- **Prompt/tool injection defense (§9.5)**: retrieved text isolated as untrusted data; system instruction
  delimiter-separated; in-document tool-call syntax never executed; native tool calls still pass Tool
  Broker; never forward URL/command/SQL strings to a generic executor; tool result masked + size/field
  allowlisted before returning to model; detect repeated suspicious tool calls + budget exhaustion.
- **Secret protection (§9.6)**: reuse existing `SecretProvider`. AI trace stores only `secret_name,
secret_version, provider_profile, resolution_timestamp` — never the value. Adapter sanitizes provider
  error bodies of secrets / raw Authorization headers.
- **Logging/privacy (§9.7)**: raw prompt + tool result are NOT stored in general audit JSON. General DB =
  hash/redacted preview/counts/IDs; encrypted prompt artifact = separate access permission + short
  explicit retention + legal-hold + erasure-request lineage + export marking. Hidden chain-of-thought is
  not stored; stored = input, compiled context manifest, tool call/result, final answer, policy decision,
  short execution summary.

---

## Deterministic security tests (§15.2) — canonical names

`cross_tenant_object_never_retrieved · masked_property_never_compiled ·
unauthorized_content_not_ranked_or_returned · provider_egress_denied_before_network_call ·
secret_value_never_logged · tool_not_in_agent_manifest_rejected ·
write_tool_requires_confirmation_or_review · proposal_fingerprint_change_requires_new_review ·
approval_rechecks_object_version`

## Contract tests (§15.1)

Each new port needs one: `LanguageModelAdapter, ModelRegistryRepository, AiDefinitionRepository,
AiRunRepository, ContextProvider, ToolExecutor, UsageMeter`.

## CI quality gates (§15.6)

Add granular gates: `quality:ai-contracts, quality:model-gateway, quality:ai-ledger,
quality:retrieval-security, quality:context-compiler, quality:tool-broker, quality:action-proposal,
quality:approval-execution, quality:ai-operations, quality:logic-runtime, quality:ai-evals,
quality:ai-release, quality:visual-builder, quality:builder-runtime, quality:agent-runtime`. Release gate splits static / unit /
integration / **live provider smoke** (live smoke = separate lane needing credentials + cost).

## Operational failure semantics (§16.3)

`model timeout` retryable (same run attempt chain) · `egress denied` non-retryable policy failure ·
`context hash mismatch` fail closed · `tool unavailable` agent policy decides degrade-to-read-only ·
`action version conflict` needs new proposal · `review expired` needs new review · `provider usage
limit` retry-after + operator evidence · `start unknown` reuse workflow-ledger pattern.

---

## Implementation roadmap (§14) & recommended PR order (§14.7)

Phases: **P0** security+contracts · **P1** read-only Copilot · **P2** Action Proposal + Human Review ·
**P3** Logic Runtime + Temporal · **P4** Evals + Release Governance · **P5** Visual Builder (last).

Recommended PR sequence (granular): 1 `ai-contracts` (domain DTO + ports only) · 2 `ai-schema-ledger`
(migration/repository/contract tests) · 3 `model-gateway-fake` (fake adapter + deterministic tests) ·
4 `model-gateway-provider` (first provider-compatible adapter) · 5 `retrieval-security` (content/object
security token contract — **done as P0a**) · 6 `context-compiler` · 7 `agent-runtime-readonly` (bounded
loop + citations) · 8 `aip-api-sdk` · 9 `action-proposal` · 10 `approval-execution` · 11 `ai-operations`
(run/event detail, usage, trace) · 12 `logic-runtime` · 13 `aip-evals` · 14 `visual-builder` (last).

### P0 exit gate (§14.1.2)

application layer imports zero provider SDK · zero unauthorized context reaches provider ·
model/prompt/context/tool/policy hash stored · failure contract test exists · raw-secret logging test passes.

---

## Anti-patterns — never implement (§17.1)

1 FastAPI route calling provider SDK directly · 2 executing LLM-produced SQL · 3 injecting
repository/SQLAlchemy engine into Agent Runtime · 4 inserting a search index hit into the prompt without
DB validation · 5 trusting model-produced URL/citation as-is · 6 running a user tool with service-account
permission · 7 overwriting a mutable prompt/model alias · 8 storing raw prompt+PII unredacted in general
audit JSON · 9 treating the vector index as source of truth · 10 offering a generic HTTP/shell/python
tool · 11 starting from a visual canvas / multi-agent · 12 silent model fallback in a write-producing
agent · 13 changing parameters after human approval before executing · 14 treating workflow success
state as the domain commit.

## Proposed ADRs (§20)

ADR-AIP-001 LLM is untrusted planner · -002 AI runtime never calls repositories directly · -003 all
writes go through Ontology Action · -004 retrieval index is rebuildable projection · -005 prompt/context/
tool/model pinned by immutable version · -006 search security requires candidate pre-filter · -007
high-risk write requires exact-proposal human approval · -008 AI observability uses structured event
ledger · -009 raw prompts use separate encrypted artifact + retention · -010 agent release requires eval
evidence + rollback pointer.

## First product vertical slice (§13) — Order Operations Copilot

Query "explain why PO-1042 is delayed + financial impact + relevant contract clauses, and if approvable
make an approval proposal." Tools: `ontology.get_object(PurchaseOrder)`, `ontology.get_links(Supplier,
Shipment)`, `content.search(supplier_contracts)`, `action.propose(ApproveOrder)`. Acceptance (§13.2):
answers from object/link/document with resolvable citation per claim; ApproveOrder proposal created;
Action executed only after review; reflected in materialization; no unauthorized object/property/document
reaches provider; reviewer must be able to see sources; reproducible via version pins; failed
model/tool/review/action stages distinguished in Operations.

## Palantir source register (§18, appendix A)

P-01 AIP/Foundry/Apollo roles · P-02 AIP architecture overview (12 capabilities) · P-03 Ontology system ·
P-04 Chatbot Studio (prompt compilation, context window) · P-05 Retrieval context (deterministic, types) ·
P-06 Application state · P-07 Chatbot tools · P-08 AIP Logic blocks · P-09 Action types · P-10 Action
permissions · P-11 Citations · P-12 Session logging · P-13 AIP observability · P-14 Model Catalog · P-15
LLM provider-compatible APIs · P-16 AIP Evals · P-17 Apollo · P-18 Ontology-augmented generation (hybrid
search, HyDE, augmentation).
