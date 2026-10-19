"use client";

import type { Analysis } from "@/lib/api";

const stages = {
  provider: "模型生成",
  sql_guard: "SQL 校验",
  query: "查询执行",
  persist: "结果保存",
};

export function AnalysisDiagnostics({ analysis }: { analysis: Analysis }) {
  const timings = analysis.telemetry?.timings_ms;
  const usage = analysis.telemetry?.usage;
  if (!analysis.request_id && !timings) return null;
  return (
    <details className="rounded-md border border-slate-200 p-3 text-xs text-slate-500">
      <summary className="cursor-pointer font-medium text-slate-600">
        执行详情
      </summary>
      <div className="mt-3 space-y-2" aria-label="执行诊断">
        {analysis.request_id && (
          <p className="break-all">请求 ID：{analysis.request_id}</p>
        )}
        <div className="flex flex-wrap gap-x-5 gap-y-2">
          {Object.entries(stages).map(([key, label]) => (
            <span key={key}>
              {label}：
              {timings?.[key] == null ? "未执行或未记录" : `${timings[key]} ms`}
            </span>
          ))}
        </div>
        <p>
          {usage?.source === "mock"
            ? "Mock 模板未调用模型 API（Token 0）。"
            : usage?.source === "provider"
              ? `Token（提供商上报）：输入 ${usage.prompt_tokens ?? "未知"} · 输出 ${usage.completion_tokens ?? "未知"} · 总计 ${usage.total_tokens ?? "未知"}。`
              : "提供商未上报有效 Token 用量，不能据此估算费用。"}
        </p>
        <p>分段耗时不含所有请求开销；Token 不等于账单费用。</p>
      </div>
    </details>
  );
}
