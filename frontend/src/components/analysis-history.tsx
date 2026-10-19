"use client";

import { useEffect, useState, type FormEvent } from "react";
import { api, type Analysis } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export const analysisStatuses = {
  running: "执行中",
  success: "成功",
  rejected: "安全拒绝",
  error: "失败",
};

const pageSize = 20;

export function AnalysisHistory({
  datasetId,
  revision,
  activeId,
  disabled,
  onSelect,
}: {
  datasetId: string;
  revision: number;
  activeId?: string;
  disabled: boolean;
  onSelect: (item: Analysis) => void;
}) {
  const [keyword, setKeyword] = useState("");
  const [status, setStatus] = useState("");
  const [filter, setFilter] = useState({ q: "", status: "", offset: 0 });
  const [response, setResponse] = useState({
    key: "",
    items: [] as Analysis[],
    error: "",
  });
  const [retry, setRetry] = useState(0);
  const params = new URLSearchParams({
    dataset_id: datasetId,
    limit: String(pageSize + 1),
    offset: String(filter.offset),
    q: filter.q,
  });
  if (filter.status) params.set("status", filter.status);
  const path = `/analyses?${params}`;
  const requestKey = JSON.stringify([path, revision, retry]);
  const loading = response.key !== requestKey;
  const { items, error } = response;

  useEffect(() => {
    let current = true;
    api<Analysis[]>(path)
      .then((rows) => {
        if (current) setResponse({ key: requestKey, items: rows, error: "" });
      })
      .catch((e) => {
        if (current)
          setResponse({ key: requestKey, items: [], error: e.message });
      });
    return () => {
      // Late responses from an older filter must not replace the current page.
      current = false;
    };
  }, [path, requestKey]);

  function changeFilter(next: typeof filter) {
    setFilter(next);
    setRetry((value) => value + 1);
  }

  function search(event: FormEvent) {
    event.preventDefault();
    changeFilter({ q: keyword.trim(), status, offset: 0 });
  }

  const locked = disabled || loading;

  return (
    <Card role="region" aria-label="分析历史" className="shadow-none">
      <CardHeader>
        <CardTitle className="text-base">分析历史</CardTitle>
        <p className="text-xs text-slate-500">
          搜索当前数据集的问题或 SQL，每页 20 条，按创建时间倒序。
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        <form onSubmit={search} className="flex flex-wrap items-end gap-3">
          <label className="min-w-0 basis-full text-xs text-slate-500 sm:flex-1 sm:basis-0">
            历史关键词
            <input
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              maxLength={200}
              disabled={disabled}
              placeholder="搜索问题或 SQL"
              className="mt-1 w-full rounded-md border border-slate-200 p-2 text-sm text-slate-900"
            />
          </label>
          <label className="text-xs text-slate-500">
            执行状态
            <select
              value={status}
              onChange={(event) => setStatus(event.target.value)}
              disabled={disabled}
              className="mt-1 block rounded-md border border-slate-200 p-2 text-sm text-slate-900"
            >
              <option value="">全部状态</option>
              {Object.entries(analysisStatuses).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <Button type="submit" variant="outline" size="sm" disabled={disabled}>
            搜索历史
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={disabled}
            onClick={() => {
              setKeyword("");
              setStatus("");
              changeFilter({ q: "", status: "", offset: 0 });
            }}
          >
            重置筛选
          </Button>
        </form>
        <div aria-busy={loading} className="min-h-16">
          {loading ? (
            <p role="status" className="py-4 text-sm text-slate-500">
              正在加载历史…
            </p>
          ) : error ? (
            <div role="alert" className="text-sm text-red-800">
              {error}
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setRetry((value) => value + 1)}
              >
                重试历史加载
              </Button>
            </div>
          ) : items.length === 0 ? (
            <p className="py-4 text-sm text-slate-500">
              没有符合条件的分析历史。
            </p>
          ) : (
            <div className="max-h-64 space-y-1 overflow-auto">
              {items.slice(0, pageSize).map((item) => (
                <button
                  key={item.id}
                  disabled={disabled}
                  onClick={() => onSelect(item)}
                  className={`flex w-full items-center justify-between gap-4 rounded-lg p-3 text-left text-sm hover:bg-slate-50 ${item.id === activeId ? "bg-slate-50" : ""}`}
                >
                  <span className="min-w-0">
                    <span className="block truncate font-medium">
                      {item.question}
                    </span>
                    <span className="mt-1 block text-xs text-slate-400">
                      {new Date(item.created_at).toLocaleString("zh-CN")} ·{" "}
                      {item.duration_ms} ms
                    </span>
                  </span>
                  <span
                    className={`whitespace-nowrap text-xs ${item.status === "success" ? "text-emerald-700" : "text-red-700"}`}
                  >
                    {analysisStatuses[item.status]}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="flex items-center justify-end gap-3 text-xs text-slate-500">
          <Button
            size="sm"
            variant="outline"
            disabled={locked || filter.offset === 0}
            onClick={() =>
              changeFilter({ ...filter, offset: filter.offset - pageSize })
            }
          >
            上一页
          </Button>
          <span>第 {filter.offset / pageSize + 1} 页</span>
          <Button
            size="sm"
            variant="outline"
            disabled={locked || !!error || items.length <= pageSize}
            onClick={() =>
              changeFilter({ ...filter, offset: filter.offset + pageSize })
            }
          >
            下一页
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
