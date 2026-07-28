"""Runtime capabilities enabled by the production Pipeline Graph v2 compiler."""

DEFAULT_PIPELINE_RUNTIME_CAPABILITIES = frozenset(
    {
        "content_pipeline_runtime",
        "geospatial_pipeline_runtime",
        "governed_model_gateway_runtime",
        "media_output_runtime",
        "media_pipeline_runtime",
        "media_processor_registry",
        "model_pipeline_runtime",
        "multimodal_bridge_runtime",
        "ontology_mapping_candidate_runtime",
        "semantic_index_candidate_runtime",
        "streaming_pipeline_runtime",
        "tabular_v1_compiler",
        "trained_model_batch_runtime",
    }
)
