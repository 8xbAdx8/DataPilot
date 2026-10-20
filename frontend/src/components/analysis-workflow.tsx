"use client";

import type { Analysis } from "@/lib/api";

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
const ragLabels: Record<string, string> = {
  not_requested: "此记录未执行知识检索",
  not_configured: "Embedding 未配置，本次使用 Schema",
  no_documents: "没有就绪知识，本次使用 Schema",
  no_match: "未找到达到阈值的知识",
  space_mismatch: "知识与当前向量模型不匹配，需重新索引",
  retrieved: "已检索到知识",
  error: "检索未完成，未伪造检索结果",
};

export function AnalysisWorkflow({ analysis }: { analysis: Analysis }) {
  const workflow = analysis.telemetry?.workflow;
  if (!workflow) return null;
  const rag = workflow.rag;
  return (
    <section
      aria-label="分析工作流"
      className="space-y-3 rounded-lg border border-slate-200 p-4 text-sm"
    >
      <h3 className="font-medium text-slate-800">工作流与知识来源</h3>
      <p className="break-words text-xs text-slate-500">
        已进入阶段：
        {workflow.nodes.map((name) => nodeLabels[name] ?? name).join(" → ")}
      </p>
      <p className="text-xs text-slate-500">
        SQL 生成 {workflow.sql_model_calls} 次 · 修复 {workflow.repair_attempts}{" "}
        次 · Agent 模型调用 {workflow.agent?.model_calls ?? 0} 次 · 工具调用 {workflow.agent?.tool_calls ?? 0} 次
      </p>
      {rag && (
        <>
          <p>
            {ragLabels[rag.status] ?? "知识检索状态未知"}；
            {rag.used
              ? "已将以下片段提供给本次 SQL 生成。"
              : "没有将知识片段用于本次 SQL 生成。"}
          </p>
          {rag.source === "fake" && (
            <p className="text-xs text-amber-800">
              当前使用测试向量，不代表真实语义模型质量。
            </p>
          )}
          {rag.used && rag.citations.length > 0 && (
            <ul aria-label="本次知识来源" className="space-y-3">
              {rag.citations.map((source) => (
                <li
                  key={source.chunk_id}
                  className="rounded-md bg-slate-50 p-3"
                >
                  <p className="break-all font-medium">{source.title}</p>
                  <p className="mt-1 break-all text-xs text-slate-500">
                    文档 {source.document_id} · 片段 {source.chunk_index + 1}
                    {source.similarity != null
                      ? ` · 余弦分数 ${source.similarity.toFixed(3)}`
                      : ""}
                  </p>
                  <blockquote className="mt-2 whitespace-pre-wrap break-words border-l-2 border-emerald-300 pl-3 text-xs text-slate-600">
                    {source.excerpt}
                  </blockquote>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
