import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "@/components/layout/AppShell";
import { AboutPage } from "@/pages/AboutPage";
import { ArticlePage } from "@/pages/ArticlePage";
import { AskPage } from "@/pages/AskPage";
import { BaseUnnamedPage } from "@/pages/BaseUnnamedPage";
import { CandidatesPage } from "@/pages/CandidatesPage";
import { DossierPage } from "@/pages/DossierPage";
import { EntitiesPage } from "@/pages/EntitiesPage";
import { InvestigationsPage } from "@/pages/InvestigationsPage";
import { JunkHoldsPage } from "@/pages/JunkHoldsPage";
import { LogsPage } from "@/pages/LogsPage";
import { MonitoringPage } from "@/pages/MonitoringPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { PairsPage } from "@/pages/PairsPage";
import { PersonPage } from "@/pages/PersonPage";
import { PersonsPage } from "@/pages/PersonsPage";
import { PoliticalPage } from "@/pages/PoliticalPage";
import { PublicationsPage } from "@/pages/PublicationsPage";
import { ReviewPage } from "@/pages/ReviewPage";
import { RfmPage } from "@/pages/RfmPage";
import { RunsPage } from "@/pages/RunsPage";
import { SentencesPage } from "@/pages/SentencesPage";
import { UnnamedPage } from "@/pages/UnnamedPage";
import { WorkPage } from "@/pages/WorkPage";

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/work" replace />} />
        <Route path="candidates" element={<CandidatesPage />} />
        <Route path="entities" element={<EntitiesPage />} />
        <Route path="publications" element={<PublicationsPage />} />
        <Route path="political" element={<PoliticalPage />} />
        <Route path="investigations" element={<InvestigationsPage />} />
        <Route path="investigations/:personKey" element={<DossierPage />} />
        <Route path="persons" element={<PersonsPage />} />
        <Route path="persons/:personId" element={<PersonPage />} />
        <Route path="articles/:articleId" element={<ArticlePage />} />
        <Route path="runs" element={<RunsPage />} />
        <Route path="logs" element={<LogsPage />} />
        <Route path="work" element={<WorkPage />} />
        <Route path="review/roles" element={<ReviewPage kind="roles" />} />
        <Route path="review/politics" element={<ReviewPage kind="politics" />} />
        <Route path="review/pairs" element={<PairsPage />} />
        <Route path="sentences" element={<SentencesPage />} />
        <Route path="unnamed" element={<UnnamedPage />} />
        <Route path="base-unnamed" element={<BaseUnnamedPage />} />
        <Route path="junk-holds" element={<JunkHoldsPage />} />
        <Route path="ask" element={<AskPage />} />
        <Route path="monitoring" element={<MonitoringPage />} />
        <Route path="rfm" element={<RfmPage />} />
        <Route path="about" element={<AboutPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
