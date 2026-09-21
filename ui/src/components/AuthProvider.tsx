import { createContext, useContext, useEffect, type ReactNode } from "react";
import { useAuthStore } from "@/lib/auth-store";
import { setAuthErrorHandler } from "@/api/client";

interface AuthContextValue {
  isAuthenticated: boolean;
}

const AuthContext = createContext<AuthContextValue>({ isAuthenticated: false });

export function AuthProvider({ children }: { children: ReactNode }) {
  const { user, loading, checkAuth, logout } = useAuthStore();

  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  useEffect(() => {
    setAuthErrorHandler(() => {
      logout().catch(() => {});
    });
    return () => setAuthErrorHandler(null);
  }, [logout]);

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center bg-bg">
        <div className="text-text-muted animate-pulse">Loading...</div>
      </div>
    );
  }

  return (
    <AuthContext.Provider value={{ isAuthenticated: !!user }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
