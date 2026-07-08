import { normalizeFoundryLiteError } from "@foundry-lite/sdk";

export function isActiveOntologyMissingError(error: unknown): boolean {
  const normalized = normalizeFoundryLiteError(error);
  return (
    normalized.code === "NOT_FOUND" &&
    normalized.message.toLowerCase().includes("active ontology not found")
  );
}

export function isDatasetMissingError(error: unknown): boolean {
  const normalized = normalizeFoundryLiteError(error);
  return (
    normalized.code === "NOT_FOUND" &&
    normalized.message.toLowerCase().includes("dataset not found")
  );
}
