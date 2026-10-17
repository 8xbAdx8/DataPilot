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

export class ApiError extends Error {
  constructor(
    message: string,
    public code?: string,
    public sheets?: string[],
  ) {
    super(message);
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, { cache: "no-store", ...init });
  } catch {
    throw new ApiError("无法连接后端，请检查服务是否启动。", "network_error");
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body?.detail;
    const message = typeof detail === "string" ? detail : detail?.message;
    throw new ApiError(
      message ?? `请求失败（HTTP ${response.status}）`,
      detail?.code,
      detail?.sheets,
    );
  }
  return body as T;
}

export function display(value: Cell | undefined): string {
  if (value == null) return "—";
  return typeof value === "number"
    ? new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 4 }).format(value)
    : String(value);
}
