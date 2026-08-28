import { createContext, type ReactNode, useContext } from "react";

const WorkshopRuntimeApplicationContext = createContext<string | null>(null);

export function WorkshopRuntimeApplicationProvider({
  applicationId,
  children,
}: {
  applicationId: string | null;
  children: ReactNode;
}) {
  return (
    <WorkshopRuntimeApplicationContext.Provider value={applicationId}>
      {children}
    </WorkshopRuntimeApplicationContext.Provider>
  );
}

export function useWorkshopRuntimeApplicationId(): string | null {
  return useContext(WorkshopRuntimeApplicationContext);
}
