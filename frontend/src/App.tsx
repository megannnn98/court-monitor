import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "@/components/layout/AppShell";
import { AboutPage } from "@/pages/AboutPage";
import { ArticlePage } from "@/pages/ArticlePage";
import { CandidatesPage } from "@/pages/CandidatesPage";
import { MonitoringPage } from "@/pages/MonitoringPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { PersonPage } from "@/pages/PersonPage";
import { PersonsPage } from "@/pages/PersonsPage";
import { RfmPage } from "@/pages/RfmPage";
import { RunsPage } from "@/pages/RunsPage";

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/candidates" replace />} />
        <Route path="candidates" element={<CandidatesPage />} />
        <Route path="persons" element={<PersonsPage />} />
        <Route path="persons/:personId" element={<PersonPage />} />
        <Route path="articles/:articleId" element={<ArticlePage />} />
        <Route path="runs" element={<RunsPage />} />
        <Route path="monitoring" element={<MonitoringPage />} />
        <Route path="rfm" element={<RfmPage />} />
        <Route path="about" element={<AboutPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
