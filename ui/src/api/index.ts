import { api } from "./client";
import type { TokenResponse, User, JobSummary, JobDetail } from "./types";

// --- Auth ---

export async function login(username: string, password: string): Promise<TokenResponse> {
  return api("/api/auth/login", {
    method: "POST",
    body: { username, password },
  });
}

export async function logout(): Promise<void> {
  return api("/api/auth/logout", { method: "POST" });
}

export async function refresh(): Promise<TokenResponse> {
  return api("/api/auth/refresh", { method: "POST" });
}

export async function getMe(): Promise<User> {
  return api("/api/auth/me");
}

// --- Jobs ---

export async function listJobs(): Promise<JobSummary[]> {
  return api("/api/jobs");
}

export async function getJob(name: string): Promise<JobDetail> {
  return api(`/api/jobs/${encodeURIComponent(name)}`);
}

export async function getJobState(name: string): Promise<JobDetail["state"]> {
  return api(`/api/jobs/${encodeURIComponent(name)}/state`);
}

export async function updateJobYaml(
  name: string,
  yamlContent: string,
  expectedMtime?: number | null
): Promise<{ ok: boolean; warnings: string[]; backup_path?: string }> {
  return api(`/api/jobs/${encodeURIComponent(name)}`, {
    method: "PUT",
    body: { yaml_content: yamlContent, expected_mtime: expectedMtime ?? null },
  });
}

export async function runJob(name: string): Promise<{ ok: boolean; task_id: string; started_at: string }> {
  return api(`/api/jobs/${encodeURIComponent(name)}/run`, { method: "POST" });
}

export async function createJob(data: {
  name: string;
  template?: string;
  category?: string;
  description?: string;
  language?: string;
  keywords?: string[];
  prompt?: string;
  output?: string;
}): Promise<any> {
  return api("/api/jobs", { method: "POST", body: data });
}

export async function listTemplates(): Promise<{ templates: any[] }> {
  return api("/api/jobs/templates");
}

export async function cancelJob(name: string): Promise<{ ok: boolean; cancelled: boolean }> {
  return api(`/api/jobs/${encodeURIComponent(name)}/cancel`, { method: "POST" });
}

export async function getJobHistory(name: string, limit = 50): Promise<any[]> {
  return api(`/api/jobs/${encodeURIComponent(name)}/history`, { query: { limit } });
}

// --- Reports ---

export async function listReports(params?: {
  page?: number;
  per_page?: number;
  category?: string;
  job?: string;
}): Promise<{ items: any[]; total: number; page: number; pages: number }> {
  return api("/api/reports", { query: params as any });
}

export async function getReportTree(): Promise<any[]> {
  return api("/api/reports/tree");
}

export async function getReportRaw(path: string): Promise<{ path: string; content: string; size: number }> {
  return api("/api/reports/raw", { query: { path } });
}

export async function searchReports(q: string, limit = 20): Promise<{ results: any[]; total: number }> {
  return api("/api/reports/search", { query: { q, limit } });
}

export async function getReportStats(): Promise<{ total: number; total_size_bytes: number; categories: number }> {
  return api("/api/reports/stats");
}

export async function getReportCategories(): Promise<string[]> {
  return api("/api/reports/categories");
}

// --- Usage ---

export interface UsageSummary {
  days: number;
  totals: {
    runs: number;
    runs_with_usage: number;
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    searches: number;
  };
  per_day: { day: string; runs: number; prompt_tokens: number; completion_tokens: number; total_tokens: number }[];
  per_job: { job_name: string; runs: number; total_tokens: number }[];
  per_model: { model: string; runs: number; prompt_tokens: number; completion_tokens: number; total_tokens: number }[];
  estimated_cost_usd: number | null;
  pricing: { input_per_1m?: number | null; output_per_1m?: number | null };
  budget?: {
    limit: number;
    spent: number;
    ratio: number;
    warn: boolean;
    exceeded: boolean;
    block_pipeline: boolean;
    tokens: number;
    has_pricing: boolean;
  };
}

export async function getUsage(days = 30): Promise<UsageSummary> {
  return api("/api/usage", { query: { days } });
}

// --- Pipeline rounds ---

export interface RoundStage {
  key: string;
  label: string;
  file: string;
  exists: boolean;
  size: number;
  mtime: number | null;
  tokens?: number;
  status: string; // done | missing | running | success | failed | cancelled | pending | skipped
}

export interface RoundArtifact {
  name: string;
  size: number;
}

export interface Round {
  date: string;
  done: number;
  total: number;
  tokens_total?: number;
  stages: RoundStage[];
  artifacts?: RoundArtifact[];
  live: {
    date: string;
    status: string;
    trigger: string;
    started_at: string;
    finished_at: string | null;
    stages: { key: string; label: string; status: string; group: string }[];
  } | null;
}

export interface RoundsResponse {
  rounds: Round[];
  running: boolean;
  groups: { name: string; stages: string[] }[];
}

export async function getRounds(limit = 14): Promise<RoundsResponse> {
  return api("/api/pipeline/rounds", { query: { limit } });
}

export async function runRound(concurrency = 3): Promise<Round["live"]> {
  return api("/api/pipeline/run", { method: "POST", body: { concurrency } });
}

export async function cancelRound(): Promise<{ ok: boolean }> {
  return api("/api/pipeline/cancel", { method: "POST" });
}

export async function retryRound(concurrency = 3): Promise<Round["live"]> {
  return api("/api/pipeline/retry", { method: "POST", body: { concurrency } });
}

// --- Account / user management ---

export async function updateUser(
  userId: number,
  payload: { role?: string; disabled?: boolean; password?: string; revoke_sessions?: boolean },
): Promise<User> {
  return api(`/api/users/${userId}`, { method: "PATCH", body: payload });
}

export async function changePassword(currentPassword: string, newPassword: string): Promise<{ ok: boolean }> {
  return api("/api/auth/change-password", {
    method: "POST",
    body: { current_password: currentPassword, new_password: newPassword },
  });
}

export async function listMySessions(): Promise<{
  sessions: { jti: string; kind: string; expires_at: number; current: boolean }[];
}> {
  return api("/api/auth/sessions");
}

export async function revokeMySession(jti: string): Promise<{ ok: boolean }> {
  return api(`/api/auth/sessions/${encodeURIComponent(jti)}`, { method: "DELETE" });
}

export async function deleteJob(name: string): Promise<{ ok: boolean; deleted: string }> {
  return api(`/api/jobs/${encodeURIComponent(name)}`, { method: "DELETE" });
}

// --- API tokens (programmatic access) ---

export interface ApiToken {
  id: number;
  name: string;
  prefix: string;
  created_at: string | null;
  last_used_at: string | null;
  expires_at: number | null;
  revoked?: boolean;
}

export async function listApiTokens(): Promise<{ tokens: ApiToken[] }> {
  return api("/api/auth/tokens");
}

export async function createApiToken(
  name: string,
  expiresDays?: number,
): Promise<ApiToken & { token: string }> {
  return api("/api/auth/tokens", {
    method: "POST",
    body: expiresDays ? { name, expires_days: expiresDays } : { name },
  });
}

export async function revokeApiToken(id: number): Promise<{ ok: boolean }> {
  return api(`/api/auth/tokens/${id}`, { method: "DELETE" });
}
