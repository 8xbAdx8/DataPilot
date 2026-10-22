"use client";

import type { Analysis } from "@/lib/api";
import { AnalysisEvidence } from "@/components/analysis-evidence";

const nodeLabels: Record<string, string> = {
  prepare: "准备",
  retrieve: "检索知识",
  plan: "规划 / 工具",
  generate: "生成 SQL",
  validate: "安全校验",
  repair: "有限修复",
  execute: "真实查询",
  check_result: "检查结果",
};
export function AnalysisWorkflow({ analysis }: { analysis: Analysis }) {
  const workflow = analysis.telemetry?.workflow;
  return (
    <section
      aria-label="分析工作流"
      className="evidence-panel min-w-0 space-y-3 rounded-lg border p-4 text-sm"
    >
      <h3 className="font-medium">工作流与知识来源</h3>
      <p className="evidence-muted break-words text-xs">
        已进入阶段：
        {workflow?.nodes?.map((name) => nodeLabels[name] ?? name).join(" → ") ??
          "未记录"}
      </p>
      <p className="evidence-muted text-xs">
        SQL 生成 {workflow?.sql_model_calls ?? "未记录"} 次 · 修复{" "}
        {workflow?.repair_attempts ?? "未记录"} 次 · Agent 模型调用{" "}
        {workflow?.agent?.model_calls ?? "未记录"} 次 · 工具调用{" "}
        {workflow?.agent?.tool_calls ?? "未记录"} 次
      </p>
      <AnalysisEvidence analysis={analysis} />
    </section>
  );
}
