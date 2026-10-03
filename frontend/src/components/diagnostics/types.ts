export type HealthStatus =
  | "HEALTHY"
  | "DEGRADED"
  | "WARNING"
  | "ERROR"
  | "STALE"
  | "WAITING"
  | "DISABLED"
  | "UNAVAILABLE"
  | "UNKNOWN"
  | "MISSING"
  | "CRITICAL";

export type DiagnosticCard = {
  key: string;
  label: string;
  status: HealthStatus | string;
  reason?: string | null;
  last_checked?: string | null;
  last_success?: string | null;
  latency_ms?: number | null;
  error_count?: number | null;
  stale_seconds?: number | null;
  metrics?: Record<string, unknown>;
};

export type DiagnosticsOverview = {
  system_status: HealthStatus | string;
  reason?: string;
  last_checked?: string;
  cards: DiagnosticCard[];
  issue_counts?: {
    by_status?: Record<string, number>;
    open_by_severity?: Record<string, number>;
  };
  resources?: Record<string, unknown>;
  polling?: {
    ui_refresh_seconds?: number;
    expensive_metrics_seconds?: number;
  };
};

export type DiagnosticIssue = {
  id: string;
  diagnostic_id: string;
  fingerprint: string;
  severity: string;
  status: string;
  category: string;
  service?: string | null;
  subsystem?: string | null;
  component?: string | null;
  module?: string | null;
  file?: string | null;
  function?: string | null;
  line?: number | null;
  location?: string | null;
  error_code?: string | null;
  message: string;
  exception_type?: string | null;
  stack_trace?: string | null;
  symbol?: string | null;
  timeframe?: string | null;
  provider?: string | null;
  endpoint?: string | null;
  expected?: string | null;
  actual?: string | null;
  first_seen?: string | null;
  last_seen?: string | null;
  occurrence_count: number;
  owner?: string | null;
  resolved_at?: string | null;
  resolution_message?: string | null;
};

export type WhyChain = {
  dataset: string;
  status: string;
  reason?: string;
  nodes: Array<{
    key: string;
    label: string;
    status: string;
    reason?: string | null;
    ok?: boolean;
    issue?: {
      id?: string;
      diagnostic_id?: string;
      message?: string;
      file?: string;
      function?: string;
      line?: number;
      location?: string;
    };
  }>;
  failure_node?: {
    key: string;
    label: string;
    status: string;
    reason?: string | null;
  } | null;
};
