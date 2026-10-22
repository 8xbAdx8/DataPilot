"use client";

import { useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DataTable } from "@/components/data-table";
import { AnalysisPanel } from "@/components/analysis-panel";
import { KnowledgePanel } from "@/components/knowledge-panel";
import { api, ApiError, Dataset, DatasetSummary, display } from "@/lib/api";

export function Workbench() {
  const [datasets, setDatasets] = useState<DatasetSummary[]>([]);
  const [selected, setSelected] = useState<Dataset | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [sheets, setSheets] = useState<string[]>([]);
  const [sheet, setSheet] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    let active = true;
    Promise.all([
      api<DatasetSummary[]>("/datasets"),
      api<{ status: string }>("/health/ready"),
    ])
      .then(async ([list]) => {
        const first = list[0]
          ? await api<Dataset>(`/datasets/${list[0].id}`)
          : null;
        if (active) {
          setDatasets(list);
          setSelected(first);
          setReady(true);
        }
      })
      .catch((e) => {
        if (active) setError(e.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  async function upload(event: FormEvent) {
    event.preventDefault();
    if (!file || busy || loading) return;
    setBusy(true);
    setError("");
    const body = new FormData();
    body.append("file", file);
    if (sheet) body.append("sheet", sheet);
    try {
      const dataset = await api<Dataset>("/datasets", { method: "POST", body });
      setDatasets((current) => [dataset, ...current]);
      setSelected(dataset);
      setConfirmDelete(false);
      setReady(true);
      setSheets([]);
      setSheet("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "上传失败。");
      if (e instanceof ApiError && e.sheets) setSheets(e.sheets);
    } finally {
      setBusy(false);
    }
  }

  async function chooseDataset(dataset: DatasetSummary) {
    if (busy || loading) return;
    setLoading(true);
    setError("");
    setConfirmDelete(false);
    try {
      setSelected(await api<Dataset>(`/datasets/${dataset.id}`));
    } catch (e) {
      setError(e instanceof Error ? e.message : "数据集加载失败。");
    } finally {
      setLoading(false);
    }
  }

  const missing =
    selected?.profile.fields.reduce((sum, f) => sum + f.missing, 0) ?? 0;

  async function deleteSelected() {
    if (!selected || busy) return;
    setBusy(true);
    setDeleting(true);
    setError("");
    try {
      await api<void>(`/datasets/${selected.id}`, { method: "DELETE" });
      const remaining = datasets.filter((d) => d.id !== selected.id);
      setDatasets(remaining);
      setSelected(null);
      if (remaining[0])
        setSelected(await api<Dataset>(`/datasets/${remaining[0].id}`));
      setConfirmDelete(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败。");
    } finally {
      setBusy(false);
      setDeleting(false);
    }
  }
  return (
    <div className="min-h-screen bg-[#f5f7fa] text-slate-900 lg:grid lg:grid-cols-[260px_1fr]">
      <aside className="flex flex-col border-r border-slate-200 bg-[#101e2c] p-6 text-white lg:min-h-screen">
        <Link
          href="/"
          className="flex items-center gap-3 text-xl font-semibold tracking-tight"
        >
          <span className="rounded-lg bg-emerald-400 px-2 py-1 text-slate-950">
            D↗
          </span>{" "}
          DataPilot
        </Link>
        <p className="mt-2 text-xs tracking-widest text-slate-400">
          AI 数据分析工作台
        </p>
        <div className="mt-10 flex items-center justify-between">
          <h2 className="text-xs font-medium tracking-widest text-slate-400">
            数据集
          </h2>
          <span className="text-xs text-slate-400">{datasets.length}</span>
        </div>
        <nav
          aria-label="数据集"
          className="mt-4 flex gap-2 overflow-auto lg:flex-col"
        >
          {datasets.map((d) => (
            <button
              key={d.id}
              disabled={busy || loading}
              onClick={() => chooseDataset(d)}
              className={`rounded-lg px-3 py-3 text-left text-sm transition-colors ${selected?.id === d.id ? "bg-white/10 text-emerald-300" : "text-slate-300 hover:bg-white/5"}`}
            >
              <span className="block max-w-48 truncate font-medium">
                ▤ &nbsp;{d.name}
              </span>
              <span className="mt-1 block pl-5 text-xs text-slate-400">
                {d.row_count.toLocaleString()} 行 · {d.column_count} 列
              </span>
            </button>
          ))}
          {!loading && !datasets.length && (
            <p className="text-sm text-slate-400">上传文件开始探索</p>
          )}
        </nav>
        <div className="mt-auto hidden pt-10 text-xs leading-6 text-slate-400 lg:block">
          数据留在你的分析环境中
          <br />
          SQL 与查询结果均可检查
        </div>
      </aside>
      <main className="min-w-0 p-5 md:p-10 xl:px-14">
        <header className="mb-8 flex flex-wrap items-center justify-between gap-4">
          <div>
            <p className="mb-2 text-xs font-semibold tracking-widest text-emerald-700">
              WORKSPACE / 数据探索
            </p>
            <h1 className="text-3xl font-semibold tracking-tight">
              让每一个答案，都有数据依据。
            </h1>
            <p className="mt-3 text-sm text-slate-500">
              导入数据，检查质量，再用自然语言提出问题。
            </p>
          </div>
          <span
            className={`rounded-full border px-3 py-1.5 text-xs ${ready ? "border-emerald-200 bg-emerald-50 text-emerald-700" : "border-slate-200 bg-white text-slate-500"}`}
          >
            ● {ready ? "服务已就绪" : loading ? "正在连接…" : "服务未就绪"}
          </span>
        </header>
        {error && (
          <div
            role="alert"
            className="mb-6 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800"
          >
            {error}
          </div>
        )}
        <Card className="mb-7 shadow-none">
          <CardHeader>
            <CardTitle className="text-base">导入你的数据</CardTitle>
            <p className="text-sm text-slate-500">
              CSV / XLSX · 默认上限 10 MiB、10 万行、100
              列。多工作表文件需选择工作表。
            </p>
          </CardHeader>
          <CardContent>
            <form
              onSubmit={upload}
              className="flex flex-col items-stretch gap-4 sm:flex-row sm:flex-wrap sm:items-center"
            >
              <label className="min-w-0 flex-1 text-sm">
                <span className="sr-only">选择数据文件</span>
                <input
                  aria-label="选择数据文件"
                  type="file"
                  accept=".csv,.xlsx"
                  disabled={busy}
                  onChange={(e) => {
                    setFile(e.target.files?.[0] ?? null);
                    setSheets([]);
                    setSheet("");
                    setError("");
                  }}
                  className="w-full rounded-lg border border-dashed border-slate-300 bg-slate-50 p-4 text-slate-600 file:mr-4 file:rounded-md file:border-0 file:bg-white file:px-3 file:py-2 file:font-medium"
                />
              </label>
              {sheets.length > 0 && (
                <label className="text-sm">
                  工作表{" "}
                  <select
                    aria-label="工作表"
                    value={sheet}
                    onChange={(e) => setSheet(e.target.value)}
                    className="rounded-md border p-2"
                  >
                    <option value="">请选择</option>
                    {sheets.map((s) => (
                      <option key={s}>{s}</option>
                    ))}
                  </select>
                </label>
              )}
              <Button
                type="submit"
                disabled={
                  !file || busy || loading || (sheets.length > 0 && !sheet)
                }
              >
                {busy
                  ? deleting
                    ? "正在删除…"
                    : "正在解析并导入…"
                  : "上传并分析字段 ↗"}
              </Button>
            </form>
          </CardContent>
        </Card>
        {loading ? (
          <p role="status" className="p-10 text-center text-slate-500">
            正在加载数据集…
          </p>
        ) : selected ? (
          <>
            <section
              aria-label="数据概览"
              className="mb-7 grid grid-cols-2 gap-4 xl:grid-cols-4"
            >
              {[
                ["当前数据集", selected.name],
                ["数据行数", selected.row_count.toLocaleString()],
                ["字段数量", String(selected.column_count)],
                ["缺失单元格", missing.toLocaleString()],
              ].map(([label, value]) => (
                <div
                  key={label}
                  className="rounded-xl border border-slate-200 bg-white p-5"
                >
                  <p className="text-xs text-slate-500">{label}</p>
                  <p
                    className="mt-3 truncate text-xl font-semibold tabular-nums"
                    title={value}
                  >
                    {value}
                  </p>
                </div>
              ))}
            </section>
            <KnowledgePanel key={`knowledge-${selected.id}`} datasetId={selected.id} />
            <AnalysisPanel key={selected.id} dataset={selected} />
            <div className="mb-5 flex flex-wrap items-center justify-end gap-3 text-xs text-slate-500">
              {confirmDelete ? (
                <>
                  <span>将删除当前数据集、分析历史及结果文件。</span>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() => setConfirmDelete(false)}
                  >
                    取消
                  </Button>
                  <Button
                    size="sm"
                    variant="destructive"
                    disabled={busy}
                    onClick={deleteSelected}
                  >
                    确认删除
                  </Button>
                </>
              ) : (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={busy}
                  onClick={() => setConfirmDelete(true)}
                >
                  删除当前数据集
                </Button>
              )}
            </div>
            <Card className="mb-7 shadow-none">
              <CardHeader>
                <CardTitle className="text-base">字段与数据质量</CardTitle>
                <p className="text-xs text-slate-500">
                  统计基于完整导入数据；表名：data
                </p>
              </CardHeader>
              <CardContent>
                <div className="overflow-auto">
                  <table className="w-full text-left text-sm">
                    <thead className="border-b text-xs text-slate-500">
                      <tr>
                        {[
                          "字段",
                          "类型",
                          "缺失值",
                          "唯一值",
                          "最小值",
                          "最大值",
                          "均值",
                        ].map((h) => (
                          <th
                            key={h}
                            className="whitespace-nowrap p-3 font-medium"
                          >
                            {h}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {selected.profile.fields.map((f) => (
                        <tr
                          key={f.name}
                          className="border-b border-slate-100 last:border-0"
                        >
                          <td className="p-3 font-medium">{f.name}</td>
                          <td className="p-3">
                            <code className="rounded bg-slate-100 px-2 py-1 text-xs text-slate-600">
                              {f.type}
                            </code>
                          </td>
                          <td className="p-3 tabular-nums">
                            {f.missing}{" "}
                            <span className="text-xs text-slate-400">
                              ({(f.missing_ratio * 100).toFixed(1)}%)
                            </span>
                          </td>
                          <td className="p-3 tabular-nums">{f.unique}</td>
                          {["min", "max", "mean"].map((k) => (
                            <td key={k} className="p-3 tabular-nums">
                              {display(f.statistics[k])}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
            <Card className="shadow-none">
              <CardHeader>
                <CardTitle className="text-base">数据预览</CardTitle>
                <p className="text-xs text-slate-500">
                  {selected.profile.notes.join(" ")}
                </p>
              </CardHeader>
              <CardContent>
                <DataTable
                  columns={selected.profile.fields.map((f) => f.name)}
                  rows={selected.profile.preview}
                />
              </CardContent>
            </Card>
          </>
        ) : (
          <div className="rounded-xl border border-dashed border-slate-300 p-14 text-center">
            <p className="text-3xl text-emerald-700">▤</p>
            <h2 className="mt-4 font-semibold">
              你的第一个洞察，从一份数据开始
            </h2>
            <p className="mt-2 text-sm text-slate-500">
              选择 CSV 或 Excel 文件，查看真实字段、缺失值和样例。
            </p>
          </div>
        )}
      </main>
    </div>
  );
}
