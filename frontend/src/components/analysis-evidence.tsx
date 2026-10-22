"use client";

import type { Analysis, EvidenceSnapshot, KnowledgeCitation } from "@/lib/api";

const candidateLabels: Record<string, string> = {
  retrieved: "找到候选",
  no_match: "未命中",
  no_documents: "没有就绪知识",
  not_configured: "Embedding 未配置",
  space_mismatch: "向量空间不匹配",
  error: "检索失败",
  not_requested: "未执行检索",
};
const applicabilityLabels: Record<string, string> = {
  resolved: "已登记定义适用",
  not_found: "未找到登记指标",
  ambiguous: "多个定义冲突，需要澄清",
  inapplicable: "定义不适用于当前字段",
  insufficient_evidence: "证据不足，需要补充定义",
  not_configured: "指标证据来源未配置",
  not_evaluated: "尚未评估",
};
const checks: Record<string, string> = {
  approved: "通过",
  rejected: "拒绝",
  not_requested: "未进行",
};
const executions: Record<string, string> = {
  not_requested: "未执行",
  started: "已开始，结果未记录",
  success: "成功",
  error: "失败",
};
const decisions: Record<string, string> = {
  pending: "尚未完成",
  answer: "回答",
  clarify: "澄清",
  reject: "拒绝",
  error: "执行或处理错误",
};
const origins: Record<string, string> = {
  vector_candidate: "初始检索候选",
  registered_catalog: "登记目录补充",
  agent_tool: "后续工具取得",
};
const count = (ids?: string[]) => (ids ? `${ids.length} 条` : "未记录");

function SourceList({
  sources,
  evidence,
}: {
  sources: KnowledgeCitation[];
  evidence?: EvidenceSnapshot;
}) {
  if (!Array.isArray(sources) || !sources.length)
    return <p className="evidence-muted">没有保存可展示的授权来源片段。</p>;
  return (
    <ul aria-label="本次知识来源" className="space-y-3">
      {sources.map((source, index) => {
        if (!source || typeof source !== "object") return null;
        const title =
          typeof source.title === "string" && source.title
            ? source.title
            : "来源标题未记录";
        const chunk =
          Number.isInteger(source.chunk_index) && source.chunk_index >= 0
            ? source.chunk_index + 1
            : "未记录";
        const origin = evidence?.source_origins?.find(
          (s) => s.chunk_id === source.chunk_id,
        )?.origin;
        return (
          <li
            key={source.chunk_id ?? `unrecorded-${index}`}
            className="evidence-source min-w-0 rounded-md border p-3"
          >
            <p className="break-words font-medium">{title}</p>
            <p className="evidence-muted mt-1 break-all text-xs">
              文档{" "}
              {typeof source.document_id === "string"
                ? source.document_id
                : "未记录"}{" "}
              · 片段 {chunk}
            </p>
            {evidence && (
              <div className="mt-2 flex flex-wrap gap-2 text-xs">
                <span>
                  {origin
                    ? (origins[origin] ?? "来源类别未记录")
                    : "来源类别未记录"}
                </span>
                <span>
                  {evidence.eligible_source_ids?.includes(source.chunk_id)
                    ? "登记适用性通过"
                    : "未记录为适用指标"}
                </span>
                <span>
                  {evidence.selected_source_ids &&
                  typeof source.chunk_id === "string"
                    ? evidence.selected_source_ids.includes(source.chunk_id)
                      ? "计划已选择"
                      : "计划未选择"
                    : "计划选择未记录"}
                </span>
                <span>
                  {evidence.context_source_ids &&
                  typeof source.chunk_id === "string"
                    ? evidence.context_source_ids.includes(source.chunk_id)
                      ? "已进入 SQL 上下文"
                      : "未进入 SQL 上下文"
                    : "上下文传递未记录"}
                </span>
                <span>
                  {evidence.used_source_ids &&
                  typeof source.chunk_id === "string"
                    ? evidence.used_source_ids.includes(source.chunk_id)
                      ? "执行成功后确认使用"
                      : "未确认指标采用"
                    : "确认使用未记录"}
                </span>
              </div>
            )}
            {typeof source.similarity === "number" &&
              Number.isFinite(source.similarity) && (
                <p className="evidence-muted mt-2 text-xs">
                  余弦相似度 {source.similarity.toFixed(3)}
                  ：仅表示向量接近，不证明业务定义正确。
                </p>
              )}
            <details className="mt-2 text-xs">
              <summary className="cursor-pointer">
                查看当次来源快照 · {title}
              </summary>
              <blockquote className="mt-2 whitespace-pre-wrap break-words border-l-2 pl-3 leading-6">
                {typeof source.excerpt === "string"
                  ? source.excerpt
                  : "来源正文未记录"}
              </blockquote>
            </details>
          </li>
        );
      })}
    </ul>
  );
}

export function AnalysisEvidence({ analysis }: { analysis: Analysis }) {
  const rag = analysis.telemetry?.workflow?.rag;
  const evidence = rag?.evidence;
  if (evidence?.version !== 1)
    return (
      <div aria-label="来源证据链" className="space-y-3">
        <p>历史记录未包含该证据：无法区分计划选择、上下文传递和执行后确认。</p>
        {rag?.status && (
          <p>
            原记录知识状态：{candidateLabels[rag.status] ?? "未记录"}
            （不推断初始向量结果）。
          </p>
        )}
        <SourceList sources={rag?.citations ?? []} />
      </div>
    );
  const catalog = evidence.source_origins?.filter(
    (s) => s.origin === "registered_catalog",
  );
  return (
    <div aria-label="来源证据链" className="min-w-0 space-y-4">
      <dl className="grid gap-3 text-sm sm:grid-cols-2">
        <div>
          <dt className="font-medium">初始检索</dt>
          <dd>
            {candidateLabels[rag?.candidate_status ?? ""] ?? "未记录初始状态"}
          </dd>
        </div>
        <div>
          <dt className="font-medium">指标目录</dt>
          <dd>
            {catalog ? `登记目录补充 ${catalog.length} 条` : "目录补充未记录"}
          </dd>
        </div>
        <div>
          <dt className="font-medium">业务适用性</dt>
          <dd>
            {applicabilityLabels[evidence.status ?? ""] ?? "适用性未记录"}
          </dd>
        </div>
        <div>
          <dt className="font-medium">计划选择</dt>
          <dd>{count(evidence.selected_source_ids)}</dd>
        </div>
        <div>
          <dt className="font-medium">SQL 上下文传递</dt>
          <dd>{count(evidence.context_source_ids)}</dd>
        </div>
        <div>
          <dt className="font-medium">执行后确认使用</dt>
          <dd>{count(evidence.used_source_ids)}</dd>
        </div>
        <div>
          <dt className="font-medium">校验与实际执行</dt>
          <dd>
            SQL 安全：{checks[evidence.sql_validation ?? ""] ?? "未记录"}
            ；指标契约：{checks[evidence.metric_validation ?? ""] ?? "未记录"}
            ；DuckDB：{executions[evidence.execution ?? ""] ?? "未记录"}
          </dd>
        </div>
        <div>
          <dt className="font-medium">最终决策</dt>
          <dd>
            {decisions[evidence.final_decision ?? ""] ?? "未记录"}
            {analysis.error_message ? `：${analysis.error_message}` : ""}
          </dd>
        </div>
      </dl>
      {rag?.candidate_stages && (
        <p className="evidence-muted text-xs">
          候选各阶段：
          {rag.candidate_stages
            .map(
              (s) =>
                `${({ vector: "向量", lexical: "词法", fused: "融合", reranked: "重排", selected: "候选截取" } as Record<string, string>)[s.stage] ?? "其他阶段"} ${s.candidates.length} 条`,
            )
            .join(" · ") || "未产生候选阶段"}
        </p>
      )}
      {!!catalog?.length && (
        <p className="text-xs">
          本次包含登记目录补充，不代表初始向量检索命中。
        </p>
      )}
      <p className="evidence-muted text-xs">
        进入上下文表示已传入 SQL
        生成调用，不证明模型内部采用。只有登记指标契约及真实执行均通过才确认指标来源；Schema
        或字段别名查询不自动确认指标采用。
      </p>
      {rag?.source === "fake" && (
        <p className="text-xs">当前使用测试向量，不代表真实语义模型质量。</p>
      )}
      <SourceList
        sources={evidence.source_snapshots ?? []}
        evidence={evidence}
      />
      <p className="evidence-muted text-xs">
        来源为当次授权记录的持久化快照；历史读取不重新检索，知识后续更新或删除不会改写本次记录。
      </p>
    </div>
  );
}
