"""OSDK application and OAuth request schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OsdkApplicationResourceRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    resource_type: str = Field(alias="resourceType")
    resource_api_name: str = Field(alias="resourceApiName")
    scopes: list[str]


class OsdkApplicationCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    app_api_name: str = Field(alias="appApiName")
    display_name: str = Field(alias="displayName")
    client_id: str | None = Field(default=None, alias="clientId")
    resources: list[OsdkApplicationResourceRequest] = Field(default_factory=list)


class OsdkApplicationResourcesUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    resources: list[OsdkApplicationResourceRequest]


class OsdkApplicationClientRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    client_id: str = Field(alias="clientId")
    redirect_uris: list[str] = Field(default_factory=list, alias="redirectUris")
    allowed_scopes: list[str] = Field(default_factory=list, alias="allowedScopes")
    access_token_ttl_seconds: int = Field(default=900, alias="accessTokenTtlSeconds")
    refresh_token_ttl_seconds: int = Field(default=2_592_000, alias="refreshTokenTtlSeconds")
    status: str = "active"


class OsdkSdkVersionCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    language: str
    package_name: str | None = Field(default=None, alias="packageName")
    requested_bump: str | None = Field(default=None, alias="requestedBump")


class OsdkSdkCompatibilityWindowCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_version_id: str = Field(alias="fromVersionId")
    to_version_id: str = Field(alias="toVersionId")
    supported_until: str | None = Field(default=None, alias="supportedUntil")


class OsdkArtifactDownloadTokenRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ttl_seconds: int = Field(default=900, alias="ttlSeconds")


class OsdkOAuthTokenRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    grant_type: Literal["authorization_code", "client_credentials", "refresh_token"] = Field(
        default="authorization_code", alias="grantType"
    )
    client_id: str = Field(alias="clientId")
    client_secret: str | None = Field(default=None, alias="clientSecret")
    tenant_id: str | None = Field(default=None, alias="tenantId")
    resource: str | None = None
    scope: str | None = None
    code: str | None = None
    refresh_token: str | None = Field(default=None, alias="refreshToken")
    redirect_uri: str | None = Field(default=None, alias="redirectUri")
    code_verifier: str | None = Field(default=None, alias="codeVerifier")


class OsdkOAuthRefreshRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    refresh_token: str = Field(alias="refreshToken")


class OsdkMcpServerConfigureRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: Literal["enabled", "disabled"]
    description_markdown: str = Field(alias="descriptionMarkdown", min_length=1, max_length=8_000)
    allowed_origins: list[str] = Field(default_factory=list, alias="allowedOrigins")


class OsdkClientSecretRotateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    reason: str | None = Field(default=None, max_length=500)
