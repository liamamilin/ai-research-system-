import type { ReactElement } from "react";
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
import { ToastProvider } from "@/lib/toast";

/** Blocks deep links to admin-only pages instead of letting every action 403. */
export function AdminOnly({ children }: { children: ReactElement }) {
  const role = useAuthStore((s) => s.user?.role);
  if (role !== "admin") {
    return (
      <div className="card p-8 text-center space-y-3">
        <div className="text-sm text-danger">403 — 该页面仅限管理员访问</div>
        <div className="text-xs text-text-muted">
          你的角色是 {role || "未知"}。请使用管理员账号登录，或从侧栏选择可用页面。
        </div>
        <a href="/" className="btn text-xs">返回总览</a>
      </div>
    );
  }
  return children;
}

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
        <Route
          path="/settings"
          element={<AdminOnly><SettingsPage /></AdminOnly>}
        />
        <Route
          path="/scheduler"
          element={<AdminOnly><SchedulerPage /></AdminOnly>}
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <ErrorBoundary>
        <ToastProvider>
          <AuthProvider>
            <AppRoutes />
          </AuthProvider>
        </ToastProvider>
      </ErrorBoundary>
    </BrowserRouter>
  );
}
