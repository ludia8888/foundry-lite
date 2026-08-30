import { createContext, type ReactNode, useContext } from "react";

import type { AppDefinition } from "../lib/app-model";

type WorkshopRuntimeContextValue = {
  applicationId: string | null;
  definition: AppDefinition;
};

const WorkshopRuntimeApplicationContext = createContext<WorkshopRuntimeContextValue | null>(null);

export function WorkshopRuntimeApplicationProvider({
  applicationId,
  definition,
  children,
}: {
  applicationId: string | null;
  definition: AppDefinition;
  children: ReactNode;
}) {
  return (
    <WorkshopRuntimeApplicationContext.Provider value={{ applicationId, definition }}>
      {children}
    </WorkshopRuntimeApplicationContext.Provider>
  );
}

export function useWorkshopRuntimeApplicationId(): string | null {
  return useContext(WorkshopRuntimeApplicationContext)?.applicationId ?? null;
}

export function useWorkshopRuntimeDefinition(): AppDefinition {
  const value = useContext(WorkshopRuntimeApplicationContext);
  if (!value) throw new Error("Workshop runtime definition context is missing");
  return value.definition;
}
