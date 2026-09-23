import { useEffect, type ReactNode } from "react";
import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { useAppStore } from "@/lib/app-store";
import { cn } from "@/lib/utils";
import { Menu } from "lucide-react";

const DESKTOP_QUERY = "(min-width: 1024px)";

function isDesktop(): boolean {
  return window.matchMedia(DESKTOP_QUERY).matches;
}

export function Layout() {
  const { sidebarOpen, toggleSidebar, closeSidebar, setSidebar } = useAppStore();

  // Keep the sidebar usable across breakpoints: collapsed on mobile (it
  // would cover content), expanded on desktop. Sync on mount as well, in
  // case the store was initialized before the viewport settled.
  useEffect(() => {
    const mq = window.matchMedia(DESKTOP_QUERY);
    setSidebar(mq.matches);
    const onChange = (e: MediaQueryListEvent) => setSidebar(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [setSidebar]);

  // Navigating only closes the drawer on mobile; on desktop the sidebar
  // stays visible (there is no hamburger to bring it back).
  const handleNavigate = () => {
    if (!isDesktop()) closeSidebar();
  };

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
      <Sidebar open={sidebarOpen} onNavigate={handleNavigate} />

      <div className={cn("flex-1 flex flex-col overflow-hidden ml-0",
        sidebarOpen ? "lg:ml-56" : "lg:ml-0")}>
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
