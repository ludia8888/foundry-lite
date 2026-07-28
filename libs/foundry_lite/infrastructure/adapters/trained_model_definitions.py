"""Built-in trained-model definitions used by local and container QA profiles."""

from foundry_lite.application.ports.trained_model_inference import (
    TrainedModelDefinition,
    TrainedModelField,
)

TRANSACTION_RISK_DEFINITION = TrainedModelDefinition(
    model_ref="demo.transaction-risk",
    display_name="Transaction risk scorer",
    branch="master",
    version="2026.07.1",
    revision="container-risk-model-r1",
    executable_reference="local://demo.transaction-risk@container-risk-model-r1",
    input_fields=(
        TrainedModelField("amount", "double"),
        TrainedModelField("country", "string", is_required=False),
        TrainedModelField("document", "mediaReference", is_required=False),
    ),
    output_fields=(
        TrainedModelField("riskScore", "double"),
        TrainedModelField("decision", "string"),
    ),
)
