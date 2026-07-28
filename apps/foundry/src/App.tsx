import { FoundryLiteProvider } from "@foundry-lite/sdk/react";
import { lazy } from "react";
import { BrowserRouter, Route, Routes } from "react-router";

import { AppShell } from "@/components/shell/AppShell";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { API_BASE_URL, DEMO_CONTEXT, DEMO_SESSION } from "@/lib/api";

const HomePage = lazy(() => import("@/features/home/HomePage"));
const ProjectsPage = lazy(() => import("@/features/projects/ProjectsPage"));
const DataConnectionPage = lazy(
  () => import("@/features/data-connection/DataConnectionPage"),
);
const DatasetsPage = lazy(() => import("@/features/datasets/DatasetsPage"));
const PipelinesPage = lazy(() => import("@/features/pipelines/PipelinesPage"));
const DocumentIntelligencePage = lazy(
  () =>
    import(
      "@/features/document-intelligence/DocumentIntelligencePage"
    ),
);
const LineagePage = lazy(() => import("@/features/lineage/LineagePage"));
const CodeRepositoriesPage = lazy(
  () => import("@/features/code/CodeRepositoriesPage"),
);
const OntologyPage = lazy(() => import("@/features/ontology/OntologyPage"));
const ObjectExplorerPage = lazy(
  () => import("@/features/objects/ObjectExplorerPage"),
);
const ActionsPage = lazy(() => import("@/features/actions/ActionsPage"));
const WorkshopPage = lazy(() => import("@/features/workshop/WorkshopPage"));
const AipPage = lazy(() => import("@/features/aip/AipPage"));
const ApprovalsPage = lazy(() => import("@/features/approvals/ApprovalsPage"));
const DeveloperConsolePage = lazy(
  () => import("@/features/developer/DeveloperConsolePage"),
);
const MarketplacePage = lazy(
  () => import("@/features/marketplace/MarketplacePage"),
);
const AnalyticsPage = lazy(() => import("@/features/analytics/AnalyticsPage"));
const OperationsPage = lazy(
  () => import("@/features/operations/OperationsPage"),
);
const SecurityPage = lazy(() => import("@/features/security/SecurityPage"));

export function App() {
  return (
    <FoundryLiteProvider
      baseUrl={API_BASE_URL}
      sessionProvider={() => DEMO_SESSION}
      context={DEMO_CONTEXT}
    >
      <TooltipProvider delayDuration={300}>
        <BrowserRouter>
          <Routes>
            <Route element={<AppShell />}>
              <Route index element={<HomePage />} />
              <Route path="projects/*" element={<ProjectsPage />} />
              <Route
                path="data/connections/*"
                element={<DataConnectionPage />}
              />
              <Route path="datasets/*" element={<DatasetsPage />} />
              <Route path="pipelines/*" element={<PipelinesPage />} />
              <Route
                path="document-intelligence/*"
                element={<DocumentIntelligencePage />}
              />
              <Route path="lineage/*" element={<LineagePage />} />
              <Route path="code/*" element={<CodeRepositoriesPage />} />
              <Route path="ontology/*" element={<OntologyPage />} />
              <Route path="objects/*" element={<ObjectExplorerPage />} />
              <Route path="actions/*" element={<ActionsPage />} />
              <Route path="workshop/*" element={<WorkshopPage />} />
              <Route path="aip/*" element={<AipPage />} />
              <Route path="approvals/*" element={<ApprovalsPage />} />
              <Route path="developer/*" element={<DeveloperConsolePage />} />
              <Route path="marketplace/*" element={<MarketplacePage />} />
              <Route path="analytics/*" element={<AnalyticsPage />} />
              <Route path="operations/*" element={<OperationsPage />} />
              <Route path="security/*" element={<SecurityPage />} />
            </Route>
          </Routes>
        </BrowserRouter>
        <Toaster position="bottom-right" />
      </TooltipProvider>
    </FoundryLiteProvider>
  );
}
