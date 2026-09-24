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

export interface JobTemplate {
  key: string;
  name: string;
  filename: string;
  label: string;
  category: string;
  description: string;
  builtin: boolean;
  variables: string[];
  output_template: string;
  updated_at: string;
  job_name?: string;
  prompt?: string;
  keywords?: string[];
  language?: string;
  timeout_seconds?: number | null;
  schedule?: Record<string, any> | null;
  content: string;
}

export interface TemplateInput {
  key?: string;
  label?: string;
  category?: string;
  description?: string;
  prompt?: string;
  name?: string;
  keywords?: string[];
  language?: string;
  output?: string;
  timeout_seconds?: number;
  schedule?: { type?: string; time?: string; timezone?: string } | null;
}

export async function listTemplates(): Promise<{ templates: JobTemplate[] }> {
  return api("/api/jobs/templates");
}

export async function createTemplate(data: TemplateInput): Promise<JobTemplate> {
  return api("/api/jobs/templates", { method: "POST", body: data });
}

export async function updateTemplate(key: string, data: TemplateInput): Promise<JobTemplate> {
  return api(`/api/jobs/templates/${encodeURIComponent(key)}`, { method: "PUT", body: data });
}

export async function deleteTemplate(key: string): Promise<{ ok: boolean; key: string }> {
  return api(`/api/jobs/templates/${encodeURIComponent(key)}`, { method: "DELETE" });
}

export interface HealthCheck {
  name: string;
  status: "ok" | "warn" | "error";
  detail: string;
  [key: string]: unknown;
}

export interface HealthReport {
  status: "ok" | "warn" | "error";
  checked_at: string;
  deep: boolean;
  checks: HealthCheck[];
  errors: string[];
  warnings: string[];
  stale_locks?: string[];
}

export async function getHealth(detailed = false): Promise<HealthReport> {
  return api(detailed ? "/api/health/detailed" : "/api/health");
}

export interface JobCategory {
  name: string;
  count: number;
}

export async function listJobCategories(): Promise<{ categories: JobCategory[] }> {
  return api("/api/jobs/categories");
}

export async function cancelJob(name: string): Promise<{ ok: boolean; cancelled: boolean }> {
  return api(`/api/jobs/${encodeURIComponent(name)}/cancel`, { method: "POST" });
}

export interface PersistedRun {
  run_id: string;
  status: string;
  started_at: string;
  finished_at: string;
  events: number;
}

export interface PersistedLogRecord {
  run_id: string;
  ts: string;
  event: LogEventLike;
}

export interface LogEventLike {
  type?: string;
  level?: string;
  message?: string;
  status?: string;
  phase?: string;
  event_type?: string;
  [key: string]: unknown;
}

export async function getJobLogs(
  name: string,
  params?: { run_id?: string; limit?: number }
): Promise<{ runs: PersistedRun[]; events: PersistedLogRecord[]; run: PersistedRun | null; live: boolean }> {
  return api(`/api/jobs/${encodeURIComponent(name)}/logs`, { query: params as any });
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
  favorite?: boolean;
  tag?: string;
  unread?: boolean;
}): Promise<{ items: any[]; total: number; page: number; pages: number }> {
  return api("/api/reports", { query: params as any });
}

export async function getReportMeta(path: string): Promise<any> {
  return api("/api/reports/meta", { query: { path } });
}

export async function updateReportMeta(
  path: string,
  payload: { favorite?: boolean; tags?: string[]; read?: boolean; rating?: number; rating_note?: string },
): Promise<any> {
  return api("/api/reports/meta", { method: "PATCH", body: { path, ...payload } });
}

export async function listTags(): Promise<{ tags: { tag: string; count: number }[] }> {
  return api("/api/reports/tags");
}

export async function createShareLink(
  path: string,
  ttlHours = 168,
): Promise<{ token: string; path: string; expires_at: number; url: string }> {
  return api("/api/reports/share", {
    method: "POST",
    body: { path, ttl_hours: ttlHours },
  });
}

export async function emailReport(path: string, to?: string[]): Promise<{ ok: boolean }> {
  return api("/api/reports/email", {
    method: "POST",
    body: to && to.length ? { path, to } : { path },
  });
}

export interface SetupStatus {
  needs_admin: boolean;
  llm_configured: boolean;
  search_configured: boolean;
}

export async function getSetupStatus(): Promise<SetupStatus> {
  return api("/api/setup/status");
}

export interface QaCitation {
  index: number;
  path: string;
  title: string;
  snippet: string;
  source: string;
  score: number;
}

export interface QaAnswer {
  answer: string;
  citations: QaCitation[];
  mode: string;
  usage?: Record<string, number>;
}

export async function askQuestion(question: string, limit = 6): Promise<QaAnswer> {
  return api("/api/qa", { method: "POST", body: { question }, query: { limit } });
}

export async function reindexVectors(limit = 200): Promise<{
  embedded: number;
  skipped: number;
  failed: number;
  stats: { documents: number; chunks: number };
}> {
  return api("/api/qa/reindex", { method: "POST", query: { limit } });
}

export interface SharedReport {
  path: string;
  title: string;
  content: string;
  size: number;
  expires_at: number | null;
  shared_by: string;
}

export async function getSharedReport(token: string): Promise<SharedReport> {
  return api(`/api/share/${encodeURIComponent(token)}`);
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
  per_user?: { user: string; runs: number; total_tokens: number; searches: number }[];
  budget?: {
    limit: number;
    spent: number;
    ratio: number;
    warn: boolean;
    exceeded: boolean;
    block_pipeline: boolean;
    tokens: number;
    has_pricing: boolean;
    window_start?: string;
    per_user?: { user: string; runs: number; total_tokens: number; searches: number }[];
  };
}

export async function getUsage(days = 30, month = false): Promise<UsageSummary> {
  return api("/api/usage", { query: month ? { month: "true" } : { days } });
}

export interface RatingSummary {
  count: number;
  average: number | null;
  per_job: { job_name: string; count: number; average: number }[];
}

export async function getRatings(): Promise<RatingSummary> {
  return api("/api/usage/ratings");
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

export async function retryRound(
  concurrency = 3,
  invalidateDownstream = true
): Promise<{ invalidated_stages?: string[]; rerun_stages?: string[] }> {
  return api("/api/pipeline/retry", {
    method: "POST",
    body: { concurrency, date: null, invalidate_downstream: invalidateDownstream },
  });
}

// --- Round item tracking ---

export interface TrackedItem {
  id: string;
  kind: "action" | "test" | "watch";
  text: string;
  priority: string;
  first_seen: string;
  last_seen: string;
  times_seen: number;
  status: "open" | "done" | "dropped";
  note: string;
  updated_at: string;
}

export interface CarryOver {
  date: string | null;
  new: TrackedItem[];
  continuing: TrackedItem[];
  open_stale: TrackedItem[];
}

export async function getTrackedItems(params?: {
  kind?: string;
  status?: string;
  limit?: number;
}): Promise<{ items: TrackedItem[]; carry_over: CarryOver }> {
  return api("/api/pipeline/actions", { query: params as Record<string, string | number | undefined> });
}

export async function updateTrackedItem(
  id: string,
  payload: { status?: string; note?: string },
): Promise<TrackedItem> {
  return api(`/api/pipeline/actions/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: payload,
  });
}

export interface DiffRow {
  [key: string]: string;
}

export interface RoundDiff {
  from: string | null;
  to: string;
  actions: { added: DiffRow[]; removed: DiffRow[]; persisted: DiffRow[] };
  tests: { added: DiffRow[]; removed: DiffRow[]; persisted: DiffRow[] };
  watchlist: { added: DiffRow[]; removed: DiffRow[]; persisted: DiffRow[] };
  sources: { added: string[]; removed: string[]; new_domains: string[] };
  counts: Record<string, number>;
}

export async function getRoundDiff(date: string, against?: string): Promise<RoundDiff> {
  return api(`/api/pipeline/rounds/${encodeURIComponent(date)}/diff`, {
    query: against ? { against } : undefined,
  });
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
