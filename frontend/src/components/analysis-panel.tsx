"use client";

import { useEffect, useState, type FormEvent } from "react";
import { api, ApiError, type Analysis, type Dataset } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DataTable } from "@/components/data-table";
import { ResultChart } from "@/components/result-chart";
import { AnalysisDiagnostics } from "@/components/analysis-diagnostics";
import { AnalysisWorkflow } from "@/components/analysis-workflow";
import {
  AnalysisHistory,
  analysisStatuses,
} from "@/components/analysis-history";

export function AnalysisPanel({ dataset }: { dataset: Dataset }) {
  const [question, setQuestion] = useState("");
  const [active, setActive] = useState<Analysis | null>(null);
  const [historyVersion, setHistoryVersion] = useState(0);
  const [provider, setProvider] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let mounted = true;
    api<{ provider: string }>("/config")
      .then((config) => {
        if (mounted) {
          setProvider(config.provider);
        }
      })
      .catch((e) => {
        if (mounted) setError(e.message);
      });
    return () => {
      mounted = false;
    };
  }, [dataset.id]);

  async function analyze(event: FormEvent) {
    event.preventDefault();
    if (!question.trim() || busy) return;
    setBusy(true);
    setError("");
    setActive(null);
    try {
      const item = await api<Analysis>(`/datasets/${dataset.id}/analyses`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      });
      setActive(item);
    } catch (e) {
      setError(
        e instanceof ApiError && e.requestId
          ? `${e.message}（请求 ID：${e.requestId}）`
          : e instanceof Error
            ? e.message
            : "分析失败。",
      );
      if (e instanceof ApiError && e.analysisId) {
        try {
          setActive(await api<Analysis>(`/analyses/${e.analysisId}`));
        } catch {
          /* Original failure remains visible. */
        }
      }
    } finally {
      setHistoryVersion((version) => version + 1);
      setBusy(false);
    }
  }

  async function loadHistory(item: Analysis) {
    setBusy(true);
    setError("");
    try {
      setActive(await api<Analysis>(`/analyses/${item.id}`));
      setQuestion(item.question);
    } catch (e) {
      setError(e instanceof Error ? e.message : "历史加载失败。");
    } finally {
      setBusy(false);
    }
  }

  const metric = dataset.profile.fields.find((f) =>
    /INT|FLOAT|DOUBLE|DECIMAL/.test(f.type),
  );
  const dimension = dataset.profile.fields.find(
    (f) => !/INT|FLOAT|DOUBLE|DECIMAL/.test(f.type),
  );
  const suggestions = [
    "这个数据集有多少行？",
    "预览数据",
    ...(metric && dimension
      ? [`按 ${dimension.name} 统计 ${metric.name} 总和`]
      : []),
  ];

  return (
    <section aria-label="自然语言分析" className="mb-7 space-y-5">
      <Card className="shadow-none">
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <CardTitle className="text-base">向数据提问</CardTitle>
            <span className="rounded-md bg-emerald-50 px-2 py-1 text-xs text-emerald-800">
              {provider === "mock"
                ? "Mock · 确定性模板"
                : provider || "正在读取模型配置…"}
            </span>
          </div>
          <p className="text-sm text-slate-500">
            系统生成 SQL，经安全校验后执行真实查询。当前只支持单表分析。
          </p>
        </CardHeader>
        <CardContent>
          <form onSubmit={analyze}>
            <label htmlFor="analysis-question" className="sr-only">
              分析问题
            </label>
            <textarea
              id="analysis-question"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              maxLength={1000}
              disabled={busy}
              rows={3}
              placeholder="例如：按 region 统计 revenue 总和"
              className="w-full resize-y rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-100"
            />
            <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap gap-2">
                {suggestions.map((s) => (
                  <button
                    key={s}
                    type="button"
                    disabled={busy}
                    onClick={() => setQuestion(s)}
                    className="rounded-full border border-slate-200 px-3 py-1 text-xs text-slate-500 hover:border-emerald-300 hover:text-emerald-800"
                  >
                    {s}
                  </button>
                ))}
              </div>
              <Button disabled={busy || !question.trim()}>
                {busy ? "正在分析…" : "执行分析 ↗"}
              </Button>
            </div>
          </form>
          {busy && (
            <p role="status" className="mt-4 text-sm text-emerald-700">
              正在读取模型输出、校验 SQL 并执行查询…
            </p>
          )}
          {error && (
            <p
              role="alert"
              className="mt-4 rounded-md bg-red-50 p-3 text-sm text-red-800"
            >
              {error}
            </p>
          )}
        </CardContent>
      </Card>
      {active && (
        <Card className="shadow-none">
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <CardTitle className="text-base">分析结果</CardTitle>
              <span
                className={`rounded-full px-3 py-1 text-xs ${active.status === "success" ? "bg-emerald-50 text-emerald-800" : "bg-red-50 text-red-800"}`}
              >
                {analysisStatuses[active.status]} · {active.duration_ms} ms ·{" "}
                {active.provider}
              </span>
            </div>
          </CardHeader>
          <CardContent className="space-y-5">
            <AnalysisDiagnostics analysis={active} />
            <AnalysisWorkflow analysis={active} />
            {active.status === "clarification" && (
              <div
                role="status"
                className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950"
              >
                <p className="mb-2 font-medium">
                  需要补充信息，本次尚未生成或执行 SQL。
                </p>
                <ul className="list-inside list-disc">
                  {active.summary.clarification_questions?.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
                <p className="mt-2">请在上方问题中补充后重新提交。</p>
              </div>
            )}
            {active.sql && (
              <div>
                <p className="mb-2 text-xs font-medium text-slate-500">
                  {active.status === "success"
                    ? "已校验并执行的 SQL"
                    : "生成的 SQL（未成功执行）"}
                </p>
                <pre
                  aria-label="SQL"
                  className="overflow-auto whitespace-pre-wrap break-words rounded-lg bg-[#101e2c] p-4 font-mono text-sm leading-6 text-emerald-200"
                >
                  <code>{active.sql}</code>
                </pre>
                <p className="mt-2 text-xs text-slate-500">
                  模型对 SQL 的说明：{active.explanation}
                </p>
              </div>
            )}
            {active.status !== "success" && (
              <p className="text-sm text-red-800">
                {active.error_message || "尚未生成成功结果。"}
              </p>
            )}
            {active.result_expired && (
              <p className="rounded-md bg-amber-50 p-3 text-sm text-amber-800">
                结果快照已清理，历史元信息仍保留。重新执行问题可生成新结果。
              </p>
            )}
            {active.result && (
              <>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <p className="text-sm text-slate-500">
                    实际返回 {active.returned_rows} 行
                    {active.truncated
                      ? " · 已达到行数限制，仅显示部分结果"
                      : ""}
                  </p>
                  <Button asChild variant="outline" size="sm">
                    <a href={`/api/analyses/${active.id}/export`} download>
                      导出结果 CSV ↓
                    </a>
                  </Button>
                </div>
                <ResultChart result={active.result} />
                <DataTable
                  columns={active.result.columns.map((c) => c.name)}
                  rows={active.result.rows}
                />
              </>
            )}
            {active.status === "success" && (
              <div className="grid gap-4 rounded-lg bg-slate-50 p-4 text-sm md:grid-cols-3">
                <div>
                  <h3 className="mb-2 font-medium text-emerald-800">
                    事实 · 来自查询结果
                  </h3>
                  {active.summary.facts?.map((f) => (
                    <p
                      key={f}
                      className="mb-2 break-words leading-6 text-slate-600"
                    >
                      {f}
                    </p>
                  ))}
                </div>
                <div>
                  <h3 className="mb-2 font-medium">推测</h3>
                  <p className="leading-6 text-slate-500">
                    未进行推测或因果推断。
                  </p>
                </div>
                <div>
                  <h3 className="mb-2 font-medium">局限性</h3>
                  {active.summary.limitations?.map((l) => (
                    <p key={l} className="mb-2 leading-6 text-slate-500">
                      {l}
                    </p>
                  ))}
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      )}
      <AnalysisHistory
        datasetId={dataset.id}
        revision={historyVersion}
        activeId={active?.id}
        disabled={busy}
        onSelect={loadHistory}
      />
    </section>
  );
}
