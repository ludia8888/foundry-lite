import type {
  ConnectorResource,
  SourceConnection,
  SourceManagedSync,
} from "@foundry-lite/sdk";
import { idempotencyKey } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteMutation,
  useFoundryLiteQuery,
} from "@foundry-lite/sdk/react";
import {
  type Dispatch,
  type SetStateAction,
  useCallback,
  useEffect,
  useState,
} from "react";

import { sanitizeIdentifier } from "../source-model";
import {
  buildManagedSyncCreateRequest,
  createInitialNewSyncDraft,
  evaluateNewSyncDraft,
  findRestResource,
  type NewSyncDraft,
  type NewSyncDraftUpdater,
  type NewSyncSourceKinds,
  type NewSyncValidation,
  sourceKinds,
} from "./new-sync-model";

interface NewSyncEditorControllerInput {
  source: SourceConnection;
  initialTableName?: string;
  initialResourceName?: string;
  onCreated: (sync: SourceManagedSync) => void;
}

export interface NewSyncEditorController {
  draft: NewSyncDraft;
  updateDraft: NewSyncDraftUpdater;
  kinds: NewSyncSourceKinds;
  syncName: string;
  idempotencyRef: string;
  validation: NewSyncValidation;
  restResources: readonly ConnectorResource[];
  selectedRestResource: ConnectorResource | null;
  hasRestResourceMismatch: boolean;
  restError: unknown;
  createError: unknown;
  isCreating: boolean;
  create: () => void;
  reloadRestResources: () => void;
}

export function useNewSyncEditorController({
  source,
  initialTableName,
  initialResourceName,
  onCreated,
}: NewSyncEditorControllerInput): NewSyncEditorController {
  const client = useFoundryLiteClient();
  const kinds = sourceKinds(source);
  const [draft, setDraft] = useState<NewSyncDraft>(() =>
    createInitialNewSyncDraft(
      source,
      initialTableName,
      initialResourceName,
    ),
  );
  const updateDraft = useCallback<NewSyncDraftUpdater>((field, value) => {
    setDraft((current) => ({ ...current, [field]: value }));
  }, []);
  const [idempotencyRef] = useState(() =>
    idempotencyKey("managed-sync", crypto.randomUUID()),
  );
  const syncName = sanitizeIdentifier(draft.displayName);
  const restConnectorQuery = useFoundryLiteQuery(
    ["data-connection", "rest-connector", draft.connectorName.trim()],
    () =>
      kinds.isRest && draft.connectorName.trim()
        ? client.connectors.connections.get(draft.connectorName.trim())
        : Promise.resolve(null),
    { enabled: kinds.isRest && draft.connectorName.trim().length > 0 },
  );
  const restResources = restConnectorQuery.data?.resources ?? [];
  const selectedRestResource = findRestResource(
    restResources,
    draft.resourceName,
  );
  const hasRestResourceMismatch =
    selectedRestResource !== null &&
    draft.datasetRef.trim().length > 0 &&
    draft.datasetRef.trim() !== selectedRestResource.datasetRef;

  useSelectedRestDataset(
    kinds.isRest,
    selectedRestResource,
    hasRestResourceMismatch,
    draft.isDatasetRefTouched,
    setDraft,
  );

  const createSync = useFoundryLiteMutation(
    useCallback(
      () =>
        client.sources.managedSyncs.create(
          buildManagedSyncCreateRequest(source, draft, syncName),
          { idempotencyKey: idempotencyRef },
        ),
      [client, draft, idempotencyRef, source, syncName],
    ),
    { onSuccess: (sync) => onCreated(sync) },
  );
  const validation = evaluateNewSyncDraft(draft, kinds, syncName, {
    hasRestResourceMismatch,
    hasRestConnectorError: Boolean(restConnectorQuery.error),
  });

  return {
    draft,
    updateDraft,
    kinds,
    syncName,
    idempotencyRef,
    validation,
    restResources,
    selectedRestResource,
    hasRestResourceMismatch,
    restError: restConnectorQuery.error,
    createError: createSync.error,
    isCreating: createSync.isRunning,
    create: () => void createSync.execute(undefined),
    reloadRestResources: () => void restConnectorQuery.reload(),
  };
}

function useSelectedRestDataset(
  isRest: boolean,
  selectedResource: ConnectorResource | null,
  hasResourceMismatch: boolean,
  isDatasetRefTouched: boolean,
  setDraft: Dispatch<SetStateAction<NewSyncDraft>>,
): void {
  useEffect(() => {
    if (!isRest || selectedResource === null) return;
    if (isDatasetRefTouched && !hasResourceMismatch) return;
    setDraft((current) => {
      if (
        current.datasetRef === selectedResource.datasetRef &&
        !current.isDatasetRefTouched
      ) {
        return current;
      }
      return {
        ...current,
        datasetRef: selectedResource.datasetRef,
        isDatasetRefTouched: false,
      };
    });
  }, [
    hasResourceMismatch,
    isDatasetRefTouched,
    isRest,
    selectedResource,
    setDraft,
  ]);
}
