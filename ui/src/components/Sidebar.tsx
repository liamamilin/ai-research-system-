import { useState } from "react";
import { NavLink } from "react-router-dom";
import { ListOrdered, FileText, LayoutDashboard, Settings, Clock, LogOut, Radar, MessageSquareText } from "lucide-react";
import { useAuthStore } from "@/lib/auth-store";
import { cn } from "@/lib/utils";
import { AccountModal } from "./AccountModal";

const navItems = [
  { to: "/", label: "总览", icon: LayoutDashboard },
  { to: "/jobs", label: "Jobs", icon: ListOrdered },
  { to: "/rounds", label: "情报轮次", icon: Radar },
  { to: "/ask", label: "问答", icon: MessageSquareText },
  { to: "/reports", label: "报告", icon: FileText },
];

const bottomItems = [
  { to: "/scheduler", label: "周期调度", icon: Clock },
  { to: "/settings", label: "设置", icon: Settings },
];

export function Sidebar({ open, onNavigate }: { open: boolean; onNavigate?: () => void }) {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const [accountOpen, setAccountOpen] = useState(false);

  const allItems = user?.role === "admin"
    ? [...navItems, ...bottomItems]
    : navItems;

  return (
    <aside
      className={cn(
        "fixed inset-y-0 left-0 z-30 flex flex-col bg-bg-card border-r border-border transition-transform w-56",
        open ? "translate-x-0" : "-translate-x-full"
      )}
    >
      <div className="p-4 border-b border-border">
        <h2 className="font-semibold text-sm">AI Research Console</h2>
        {user && (
          <button
            onClick={() => setAccountOpen(true)}
            className="text-xs text-text-muted hover:text-text mt-1 text-left"
          >
            {user.username} · {user.role} <span className="opacity-60">（账号设置）</span>
          </button>
        )}
      </div>

      <nav className="flex-1 p-2 space-y-1">
        {allItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === "/"}
            onClick={onNavigate}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-2 px-3 py-2 rounded-md text-sm transition",
                isActive
                  ? "bg-accent text-white"
                  : "text-text-muted hover:text-text hover:bg-bg-hover"
              )
            }
          >
            <item.icon className="w-4 h-4" />
            {item.label}
          </NavLink>
        ))}
      </nav>

      <AccountModal open={accountOpen} onClose={() => setAccountOpen(false)} />

      <div className="p-2 border-t border-border">
        <button
          onClick={() => logout()}
          className="flex items-center gap-2 w-full px-3 py-2 rounded-md text-sm text-text-muted hover:text-text hover:bg-bg-hover transition"
        >
          <LogOut className="w-4 h-4" />
          退出
        </button>
      </div>
    </aside>
  );
}
