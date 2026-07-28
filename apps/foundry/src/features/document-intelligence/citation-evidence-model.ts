import {
  FoundryLiteApiError,
  type CitationNavigationEvidence as SdkCitationNavigationEvidence,
  type CitationNavigationResolution as SdkCitationNavigationResolution,
  type FoundryLiteGeneratedClient,
} from "@foundry-lite/sdk";

import type { DocumentLabBlock } from "./document-lab-model";

export type CitationNavigationEvidence = SdkCitationNavigationEvidence;
export type CitationNavigationResolution = SdkCitationNavigationResolution;

type CitationRequestClient = Pick<FoundryLiteGeneratedClient, "aip">;

export async function resolveCitationNavigation(
  client: CitationRequestClient,
  navigationRef: string,
  signal?: AbortSignal,
): Promise<CitationNavigationResolution> {
  signal?.throwIfAborted();
  const payload = await client.aip.citations.resolveNavigation({ navigationRef });
  signal?.throwIfAborted();
  return parseCitationNavigationResolution(payload);
}

export function citationEvidenceBlock(
  resolution: CitationNavigationResolution,
): DocumentLabBlock {
  const { evidence } = resolution;
  return {
    id: evidence.contentUnitId,
    pageNumber: evidence.pageNumber,
    text: resolution.displayLabel,
    bbox: evidence.bbox,
    structure: {
      role: "verified_citation",
      derivativeKind: evidence.derivativeKind,
    },
    confidence: null,
    sourceLocator: evidence.sourceLocator,
    interpretation: null,
    evidence: {
      navigationPath: resolution.navigationPath,
      contentHash: resolution.contentHash,
      processorName: evidence.processorName,
      processorVersion: evidence.processorVersion,
      processorSpecHash: evidence.processorSpecHash,
      modelName: evidence.modelName,
      modelVersion: evidence.modelVersion,
      paramsHash: evidence.paramsHash,
    },
    raw: {
      unitKind: "verified_citation",
      contentUnitId: evidence.contentUnitId,
      mediaItemVersionId: evidence.mediaItemVersionId,
      mediaDerivativeId: evidence.mediaDerivativeId,
      sourceVersion: resolution.sourceVersion,
      contentHash: resolution.contentHash,
    },
  };
}

function parseCitationNavigationResolution(
  value: unknown,
): CitationNavigationResolution {
  const response = requiredRecord(value, "response");
  const evidence = requiredRecord(response.evidence, "evidence");
  return {
    navigationPath: requiredString(response, "navigationPath"),
    sourceResourceType: requiredString(response, "sourceResourceType"),
    sourceResourceId: requiredString(response, "sourceResourceId"),
    sourceVersion: requiredString(response, "sourceVersion"),
    contentHash: requiredString(response, "contentHash"),
    displayLabel: requiredString(response, "displayLabel"),
    evidence: {
      mediaItemVersionId: requiredString(evidence, "mediaItemVersionId"),
      mediaDerivativeId: requiredString(evidence, "mediaDerivativeId"),
      contentUnitId: requiredString(evidence, "contentUnitId"),
      pageNumber: requiredPositiveInteger(evidence, "pageNumber"),
      bbox: requiredRecord(evidence.bbox, "evidence.bbox"),
      timecode: optionalRecord(evidence.timecode, "evidence.timecode"),
      sourceLocator: requiredRecord(
        evidence.sourceLocator,
        "evidence.sourceLocator",
      ),
      derivativeKind: requiredString(evidence, "derivativeKind"),
      processorName: requiredString(evidence, "processorName"),
      processorVersion: requiredString(evidence, "processorVersion"),
      processorSpecHash: requiredString(evidence, "processorSpecHash"),
      modelName: optionalString(evidence, "modelName"),
      modelVersion: optionalString(evidence, "modelVersion"),
      paramsHash: requiredString(evidence, "paramsHash"),
      securityEnvelope: requiredRecord(
        evidence.securityEnvelope,
        "evidence.securityEnvelope",
      ),
    },
  };
}

function requiredRecord(
  value: unknown,
  field: string,
): Record<string, unknown> {
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  throw invalidResponse(field);
}

function optionalRecord(
  value: unknown,
  field: string,
): Record<string, unknown> | null {
  if (value === null || value === undefined) return null;
  return requiredRecord(value, field);
}

function requiredString(
  record: Record<string, unknown>,
  field: string,
): string {
  const value = record[field];
  if (typeof value === "string" && value.length > 0) return value;
  throw invalidResponse(field);
}

function optionalString(
  record: Record<string, unknown>,
  field: string,
): string | null {
  const value = record[field];
  if (value === null || value === undefined) return null;
  if (typeof value === "string" && value.length > 0) return value;
  throw invalidResponse(field);
}

function requiredPositiveInteger(
  record: Record<string, unknown>,
  field: string,
): number {
  const value = record[field];
  if (
    typeof value === "number" &&
    Number.isInteger(value) &&
    value > 0
  ) {
    return value;
  }
  throw invalidResponse(field);
}

function invalidResponse(field: string): FoundryLiteApiError {
  return new FoundryLiteApiError(
    502,
    "INVALID_CITATION_EVIDENCE_RESPONSE",
    `Citation evidence response is missing a valid ${field}.`,
    { field },
    null,
    false,
  );
}
