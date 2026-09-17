import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Home } from "./pages/Home";
import { Results } from "./pages/Results";
import { Compare } from "./pages/Compare";
import { EntrantProfile } from "./pages/EntrantProfile";
import { TaskCatalog } from "./pages/TaskCatalog";
import { TaskDetail } from "./pages/TaskDetail";
import { RunEvidence } from "./pages/RunEvidence";
import { Methodology } from "./pages/Methodology";
import { ReleasesList } from "./pages/ReleasesList";
import { ReleaseDetail } from "./pages/ReleaseDetail";
import { Corrections } from "./pages/Corrections";
import { RunLocally } from "./pages/RunLocally";
import { NotFound } from "./pages/NotFound";
import { CampaignAdmin } from "./pages/CampaignAdmin";
import { CampaignProgress } from "./pages/CampaignProgress";
import { PublicationReview } from "./pages/PublicationReview";


const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
    },
  },
});

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Home />} />
            <Route path="results" element={<Results />} />
            <Route path="compare" element={<Compare />} />
            <Route path="entrants/:slug" element={<EntrantProfile />} />
            <Route path="tasks" element={<TaskCatalog />} />
            <Route path="tasks/:slug/:version" element={<TaskDetail />} />
            <Route path="runs/:trialId" element={<RunEvidence />} />
            <Route path="methodology" element={<Methodology />} />
            <Route path="methodology/:version" element={<Methodology />} />
            <Route path="releases" element={<ReleasesList />} />
            <Route path="releases/:publicationId" element={<ReleaseDetail />} />
            <Route path="corrections" element={<Corrections />} />
            <Route path="docs" element={<RunLocally />} />
            <Route path="admin/campaigns" element={<CampaignAdmin />} />
            <Route path="admin/campaigns/:campaignId" element={<CampaignAdmin />} />
            <Route path="admin/campaigns/:campaignId/progress" element={<CampaignProgress />} />
            <Route path="admin/campaigns/:campaignId/publications" element={<PublicationReview />} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
