import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";

import { AuthProvider } from "@/components/AuthProvider";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { Layout } from "@/components/Layout";
import { LoginPage } from "@/pages/Login";
import { DashboardPage } from "@/pages/Dashboard";
import { JobsListPage } from "@/pages/JobsList";
import { JobDetailPage } from "@/pages/JobDetail";
import { ReportsPage } from "@/pages/Reports";
import { ReportViewPage } from "@/pages/ReportView";
import { SettingsPage } from "@/pages/Settings";
import { SchedulerPage } from "@/pages/Scheduler";
import { RoundsPage } from "@/pages/Rounds";
import { AskPage } from "@/pages/Ask";
import { ShareViewPage } from "@/pages/ShareView";
import { useAuthStore } from "@/lib/auth-store";

function AppRoutes() {
  const user = useAuthStore((s) => s.user);

  if (!user) {
    return (
      <Routes>
        <Route path="/share/:token" element={<ShareViewPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }

  return (
    <Routes>
      <Route path="/share/:token" element={<ShareViewPage />} />
      <Route element={<Layout />}>
        <Route index element={<DashboardPage />} />
        <Route path="/jobs" element={<JobsListPage />} />
        <Route path="/jobs/:name" element={<JobDetailPage />} />
        <Route path="/rounds" element={<RoundsPage />} />
        <Route path="/ask" element={<AskPage />} />
        <Route path="/reports" element={<ReportsPage />} />
        <Route path="/reports/view" element={<ReportViewPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/scheduler" element={<SchedulerPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <ErrorBoundary>
        <AuthProvider>
          <AppRoutes />
        </AuthProvider>
      </ErrorBoundary>
    </BrowserRouter>
  );
}
