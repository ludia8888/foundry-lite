# ADR-0002: Public-behavior MMDP parity uses Pipeline Graph v2

- **Status:** Accepted
- **Date:** 2026-07-16
- **Scope:** Pipeline Builder, Media/Content Plane integration, execution evidence, preview, output commit, and rollout compatibility
- **Related:** [ADR-0001](0001-media-plane-parallel-to-dataset-plane.md), [Pipeline Builder parity matrix](../pipeline-builder-parity-matrix.json)

## Context

Foundry-lite already has two strong but only partly connected foundations:

1. The Dataset Plane has immutable versions, transaction-based commit, schema checks, transform execution, lineage, and Ontology handoff.
2. The Media/Content Plane has immutable media versions, derivative processing, content units, security-envelope inheritance, processor/model/spec pinning, and search projections.

The existing Pipeline Builder graph was designed mainly for Dataset nodes. It could not accurately represent a media selection, derivative set, content-unit set, vector-index generation, stream checkpoint, geospatial series, or Ontology mapping as a typed artifact. Edges also did not preserve named source and target ports, so a transform such as a join could accidentally infer meaning from array order.

Palantir's public documentation describes a different user-facing contract:

- The Multimodal Data Plane supports tabular, media, document, stream, and geospatial modalities with multiple compute and model choices.
- Pipeline Builder places a typed intermediary between authored logic and execution engines.
- Media sets can be graph inputs without first being flattened into tables and can produce either media or tabular information.
- Outputs can include datasets, media sets, virtual tables, Ontology components, time series, and geotemporal resources.
- Branch protection, proposals, reusable parameters, custom functions, checkpoints, and job groups are product-level management capabilities.

The architectural goal is therefore public-behavior parity, not private implementation replication.

## Decision

### 1. Public documentation is the external boundary

Foundry-lite will implement capabilities and interaction semantics that are visible in official Palantir documentation. It will not claim to reproduce private source code, Rubix or Apollo internals, proprietary deployment mechanisms, trademarks, screenshots, or brand assets.

The machine-readable comparison and rollout boundary is [docs/pipeline-builder-parity-matrix.json](../pipeline-builder-parity-matrix.json). A capability can be called `current` only when automated proof and operator evidence exist.

### 2. Graph v2 is the canonical new-write format

New graph authoring uses `schemaVersion: 2`.

- A node stores only its `source | transform | output` shell, `descriptorId`, `specVersion`, and typed configuration.
- An edge stores `sourceNodeId`, `sourcePortId`, `targetNodeId`, and `targetPortId`.
- A server-owned descriptor defines the accepted artifact kinds, port cardinality, configuration fields, availability, and runtime capability.
- Stored source schemas are hints, not serving truth. Validation resolves committed source versions and schemas.
- A v2 graph may contain more than one typed output.

Existing immutable v1 versions and proposals are not rewritten. A pure normalizer provides a canonical v2 view, and the v1 compiler remains available for historical replay.

### 3. Artifact identity crosses every boundary

The graph contract recognizes these artifact kinds:

- `dataset_version`
- `virtual_table`
- `media_set_selection`
- `media_derivative_set`
- `content_unit_set`
- `vector_index_generation`
- `stream_checkpoint`
- `geospatial_series`
- `ontology_mapping`

Every runtime artifact will carry a manifest and an Artifact Passport containing its kind, owning plane, immutable reference, content fingerprint, security envelope, producer node and port, processor/model/spec pins, and serving status.

### 4. Logic description, planning, and execution remain separate

The final runtime is divided into distinct responsibilities:

- normalization;
- graph validation;
- immutable plan compilation;
- deployment pinning;
- asynchronous execution;
- executor strategies for tabular, media, content, index, model, Ontology, stream, and geospatial work.

The API process must not import and run arbitrary user Python. Custom code will eventually execute through a separately governed code-execution adapter.

### 5. Each output commits through its owning plane

Dataset, Media, Index, Virtual Table, Ontology, Time Series, and Geotemporal outputs do not share one fake universal commit implementation. Each output follows its plane's `stage → validate → commit` invariant.

Intermediate artifacts are not serving assets. If one output commits and another fails, the run records `PARTIAL` and preserves the exact immutable successes and failures instead of rolling back or hiding already committed results.

### 6. Preview is explicitly non-serving

Preview executes the unsaved draft graph under bounded item, row, byte, duration, and timeout limits. Preview artifacts are marked non-serving and may never create a Dataset or Media serving version. The product must show that boundary directly to the user.

General tabular preview uses a 500-row ceiling. `Use LLM` output preview is independently capped at 50 rows. Preview sampling is not a build limit; a subsequent build still evaluates its full selected input.

### 7. Processor resolution is exact and fail-closed

Media processors are registered as discoverable descriptors with processor version, model version, input formats, output kinds, resource requirements, preview capability, and parameter schema.

Missing identities, version mismatches, and unsupported input formats are typed failures. The runtime must not silently substitute a different processor or model.

This exact registry is a Foundry-lite implementation choice that makes the public media-operation contract reproducible. It is not a claim about Palantir's private processor-registry implementation.

### 8. Rollout is additive and reversible by feature flag

The rollout order is:

1. Graph v2 contracts and shadow validation;
2. additive persistence and repository contracts;
3. tabular P0 correctness;
4. asynchronous runtime and isolation;
5. processor registry and artifact manifests;
6. no-commit preview;
7. PDF golden path;
8. Ontology and AIP evidence;
9. remaining multimodal and model nodes;
10. management, streaming, geospatial, and external compute;
11. production collaboration parity;
12. canary and default-on.

Rollback disables the feature flag or routes eligible work back to the permanent v1 compiler. It does not destructively downgrade or rewrite historical graph versions.

### 9. Streaming uses the existing open worker boundary

Streaming execution is intentionally limited to Kafka, CDC, and WebSocket ingestion plus Foundry-lite workers that persist checkpoints and enforce leases, fencing, retry, replay, and durable run evidence. Alternative streaming engines are not part of this ADR or the Pipeline Builder rollout scope.

### 10. Prompt-driven interpretation uses typed bridges

Prompt execution does not erase modality boundaries.

- Table and Content Unit rows can enter a governed model node directly.
- Image and PDF interpretation first converts a Media Set or derivative selection into table rows containing immutable `mediaReference` coordinates.
- The model node accepts only image and PDF references. Audio must first become transcript or segment rows, and video must first become frame, audio, or bounded segment artifacts.
- User and system prompts remain editable in text and vision modes. Modes may provide safe defaults but do not invent undocumented fixed-prompt locks.
- Every model node pins its model, prompt version, editable prompt mode, output schema, error mode, cache coordinates, data classification, and source locator.
- A malformed typed response is a row or item error. It is never silently accepted as an untyped value.

This preserves the public Pipeline Builder distinction between media transforms and the `Use LLM` board while still allowing semantic analysis to join structured and unstructured evidence.

### 11. Document Intelligence remains a separate experiment surface

Document Intelligence is not collapsed into a generic graph inspector. A separate Document Lab compares raw extraction, OCR, layout-aware extraction, and VLM-assisted strategies against the same source pages. It synchronizes extracted Markdown or layout blocks with original PDF bounding boxes and records quality, latency, token, and cost evidence.

Palantir's public Document Intelligence deployment path generates a Python transform repository with the selected configuration. Foundry-lite must implement that path before claiming public deployment parity. Promoting the same exact profile into a Pipeline Builder `document.extract` node remains a clearly labelled Foundry-lite extension and does not replace the generated-repository path.

The public OCR contract is structured layout content or Markdown with coordinates and confidence. `title`, `H1`, `H2`, and `body` classification is a Foundry-lite heuristic or a user-defined semantic output schema, not a Palantir fixed default.

Live video follows the same separation rule: a capture worker creates bounded segment, frame, and audio artifacts with timestamps; the graph consumes those artifacts through typed ports. A graph preview or model node does not own a long-running camera socket.

### 12. Native outputs and Foundry-lite extensions stay distinguishable

Publicly documented Pipeline Builder outputs include Dataset, Media Set, Virtual Table, Ontology object, Ontology link, time series, and geotemporal outputs. `semantic_index` is useful in Foundry-lite but is an extension, so its catalog and evidence must label it as such.

### 13. Trained-model parity is a separate node family

The public trained-model node maps one tabular input to one tabular output and may receive media references as input fields. Public documentation says preview is unavailable and static model-version pinning is planned. Foundry-lite must preserve those limitations in the catalog until an executor and mapping board exist.

## Consequences

- Dataset and Media planes remain semantically clean while meeting in one typed Builder graph.
- The browser no longer owns node contracts; it renders a server-owned descriptor catalog.
- Validation can reject artifact and port mismatches before expensive compute begins.
- Multiple output kinds become possible without pretending they share the same storage or transaction model.
- Execution evidence becomes more detailed: node runs, attempts, artifact manifests, processor/model pins, security inheritance, cancellation, retry, and partial results.
- The migration is larger than a UI redesign because it introduces a versioned IR and execution control plane.
- `current`, `foundation`, and `planned` must remain visibly distinct until browser and operator evidence closes each gap.

## Non-goals

- Pixel-for-pixel copying of Palantir screens or use of Palantir brand assets.
- Reverse engineering private services, Rubix internals, Apollo internals, or proprietary source code.
- Declaring full multimodal parity merely because Graph v2 types or database tables exist.
- Treating preview evidence as a committed Dataset or Media version.
- Running arbitrary Python inside the FastAPI process.
- Enabling every planned descriptor before an executor, failure taxonomy, security inheritance rule, and focused test exist.
- Adding a separate streaming-engine dependency outside the Kafka, CDC, WebSocket, and durable-worker boundary.
- Claiming a direct audio, raw-video, or live-video prompt node without a public product contract and an implemented typed preprocessing path.
- Presenting Foundry-lite's semantic-index output, exact processor registry, Graph-node Document Lab promotion, or hierarchy heuristics as native Palantir behavior.
