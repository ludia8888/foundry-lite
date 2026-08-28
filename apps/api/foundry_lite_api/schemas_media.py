"""Media request schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from foundry_lite_api.schema_types import JsonObject


class MediaSetCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    namespace: str
    name: str
    schema_type: str = Field(alias="schemaType")
    primary_format: str = Field(alias="primaryFormat")
    allowed_input_formats: list[str] = Field(alias="allowedInputFormats")
    classification: str
    transaction_policy: str = Field(default="transactional", alias="transactionPolicy")
    storage_profile: str = Field(default="local", alias="storageProfile")
    processing_profile: str = Field(default="local", alias="processingProfile")
    retention_policy_id: str | None = Field(default=None, alias="retentionPolicyId")


class MediaOpenTransactionRequest(BaseModel):
    mode: str = "APPEND"


class MediaProcessRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    processor: str
    processor_version: str = Field(alias="processorVersion")
    model: str | None = None
    model_version: str | None = Field(default=None, alias="modelVersion")
    parameters: JsonObject = Field(default_factory=dict)


class MediaIndexDerivativeRequest(BaseModel):
    generation: str


class MediaContentPromoteRequest(BaseModel):
    """Compare-and-swap promotion of one text content-index generation."""

    model_config = ConfigDict(populate_by_name=True)

    expected_active: str = Field(alias="expectedActive")
    generation: str


class MediaVisualPromoteRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    expected_active: str = Field(alias="expectedActive")
    generation: str


class MediaSearchRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    text: str | None = None
    top_k: int = Field(default=10, alias="topK")
    allowed_classifications: list[str] | None = Field(default=None, alias="allowedClassifications")
    # Narrow the search to these media sets. Omitting it searches every media set the caller can
    # read, which is the right default for a global search box and the wrong one for a screen
    # that already has a media set selected.
    media_set_ids: list[str] | None = Field(default=None, alias="mediaSetIds")


class MediaVisualSearchRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    text: str
    top_k: int = Field(default=10, alias="topK")


class MediaBindReferenceRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    holder_type: str = Field(alias="holderType")
    holder_id: str = Field(alias="holderId")
    property_name: str = Field(alias="propertyName")
    media_item_version_id: str = Field(alias="mediaItemVersionId")
