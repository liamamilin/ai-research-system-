export interface User {
  id: number;
  username: string;
  role: "admin" | "editor" | "viewer";
  created_at: string | null;
  last_login_at: string | null;
  disabled?: boolean;
}

export interface TokenResponse {
  user: User;
  csrf_token: string;
}

export interface JobState {
  job_name: string;
  last_run_at: string | null;
  last_status: string | null;
  last_output: string | null;
  last_error: string | null;
  last_duration_seconds: number | null;
  last_usage?: {
    model?: string;
    requests?: number;
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
    searches?: number;
  } | null;
}

export interface JobSummary {
  name: string;
  description: string;
  category: string;
  enabled: boolean;
  keywords: string[];
  output_template: string;
  state: JobState | null;
  is_running: boolean;
  /** Epoch seconds of the YAML file's last write. */
  file_mtime?: number | null;
}

export interface JobDetail extends JobSummary {
  yaml_content: string;
  yaml_mtime: number;
  file_path: string;
}
