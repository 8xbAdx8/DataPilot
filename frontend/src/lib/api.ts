export type Cell = string | number | boolean | null;
export type Field = {
  name: string;
  type: string;
  missing: number;
  missing_ratio: number;
  unique: number;
  statistics: Record<string, Cell>;
};
export type Dataset = {
  id: string;
  name: string;
  file_type: string;
  sheet_name: string | null;
  row_count: number;
  column_count: number;
  profile: {
    table_name: string;
    fields: Field[];
    preview: Record<string, Cell>[];
    notes: string[];
  };
};

export type DatasetSummary = Omit<Dataset, "profile">;
export type User = { id: string; username: string };
export type AuthSession = {
  id: string;
  created_at: string | null;
  expires_at: string;
  current: boolean;
};

export class ApiError extends Error {
  constructor(
    message: string,
    public code?: string,
    public sheets?: string[],
    public analysisId?: string,
    public requestId?: string,
    public retryAfter?: number,
  ) {
    super(message);
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    const headers = new Headers(init?.headers);
    if (
      init?.method &&
      !["GET", "HEAD", "OPTIONS"].includes(init.method.toUpperCase())
    ) {
      headers.set("X-DataPilot-CSRF", "1");
    }
    response = await fetch(`/api${path}`, {
      cache: "no-store",
      ...init,
      headers,
    });
  } catch {
    throw new ApiError("无法连接后端，请检查服务是否启动。", "network_error");
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    if (
      response.status === 401 &&
      path !== "/auth/login" &&
      path !== "/auth/me"
    ) {
      window.dispatchEvent(new Event("datapilot-session-expired"));
    }
    const detail = body?.detail;
    const message = typeof detail === "string" ? detail : detail?.message;
    throw new ApiError(
      message ?? `请求失败（HTTP ${response.status}）`,
      detail?.code,
      detail?.sheets,
      detail?.analysis_id,
      response.headers.get("X-Request-ID") ?? undefined,
      /^\d+$/.test(response.headers.get("Retry-After") ?? "")
        ? Number(response.headers.get("Retry-After"))
        : undefined,
    );
  }
  return body as T;
}

export type Summary = {
  facts?: string[];
  hypotheses?: string[];
  limitations?: string[];
};
export type QueryResult = {
  columns: { name: string; type: string }[];
  rows: Record<string, Cell>[];
  truncated: boolean;
  chart: {
    kind: "table" | "bar" | "line" | "pie";
    x?: string;
    y?: string;
    reason: string;
  };
};
export type Analysis = {
  id: string;
  dataset_id: string;
  question: string;
  sql: string | null;
  explanation: string | null;
  provider: string;
  model: string;
  status: "running" | "success" | "rejected" | "error";
  duration_ms: number;
  returned_rows: number;
  truncated: boolean;
  summary: Summary;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  request_id?: string | null;
  telemetry?: {
    timings_ms?: Record<string, number>;
    usage?: {
      source: "provider" | "mock" | "unavailable";
      prompt_tokens: number | null;
      completion_tokens: number | null;
      total_tokens: number | null;
    };
  };
  result?: QueryResult | null;
  result_expired?: boolean;
};

export function display(value: Cell | undefined): string {
  if (value == null) return "—";
  return typeof value === "number"
    ? new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 4 }).format(value)
    : String(value);
}
