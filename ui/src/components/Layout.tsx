import { useEffect, type ReactNode } from "react";
import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { useAppStore } from "@/lib/app-store";
import { Menu } from "lucide-react";

export function Layout() {
  const { sidebarOpen, toggleSidebar, closeSidebar, setSidebar } = useAppStore();

  // Keep the sidebar usable when resizing across the breakpoint:
  // collapsed on mobile (it would cover content), expanded on desktop.
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 1024px)");
    const onChange = (e: MediaQueryListEvent) => setSidebar(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [setSidebar]);

  const backdrop: ReactNode = sidebarOpen ? (
    <div
      className="fixed inset-0 z-20 bg-black/60 lg:hidden"
      onClick={closeSidebar}
      aria-hidden="true"
    />
  ) : null;

  return (
    <div className="flex h-screen overflow-hidden">
      {backdrop}
      <Sidebar open={sidebarOpen} onNavigate={closeSidebar} />

      <div className="flex-1 flex flex-col overflow-hidden ml-0 lg:ml-56">
        <header className="flex items-center gap-2 px-4 h-12 border-b border-border bg-bg-card lg:hidden">
          <button onClick={toggleSidebar} className="btn p-1" aria-label="切换侧栏">
            <Menu className="w-5 h-5" />
          </button>
          <span className="text-sm font-medium">AI Research Console</span>
        </header>

        <main className="flex-1 overflow-y-auto p-4 lg:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
