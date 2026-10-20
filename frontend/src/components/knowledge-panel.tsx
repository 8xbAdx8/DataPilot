"use client";

import { useEffect, useState, type FormEvent } from "react";
import { api, type KnowledgeDocument } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const kinds = {
  data_dictionary: "数据字典",
  field_alias: "字段别名",
  metric_definition: "指标口径",
  dataset_description: "数据集说明",
  business_rule: "业务规则",
  analysis_example: "授权分析示例",
};
const statuses = { pending: "待索引", ready: "索引就绪", failed: "索引失败" };
const reasons: Record<string, string> = {
  embedding_not_configured: "尚未配置独立的 Embedding。配置后可重新索引。",
  embedding_invalid_response: "Embedding 维度或响应不符合约定。",
  embedding_http_error: "Embedding 服务返回错误，请核对配置和余额。",
  embedding_timeout: "Embedding 请求超时。",
  embedding_unavailable: "无法连接 Embedding 服务。",
};

export function KnowledgePanel({ datasetId }: { datasetId: string }) {
  const [open, setOpen] = useState(false);
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState("field_alias");
  const [content, setContent] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<KnowledgeDocument | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    let active = true;
    api<KnowledgeDocument[]>(`/datasets/${datasetId}/knowledge`)
      .then((rows) => {
        if (active) setDocuments(rows);
      })
      .catch((failure) => {
        if (active) setError(failure.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [open, datasetId, revision]);

  function clearForm() {
    setEditing(null);
    setTitle("");
    setContent("");
  }
  function refresh() {
    setLoading(true);
    setRevision((value) => value + 1);
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const saved = await api<KnowledgeDocument>(
        editing ? `/knowledge/${editing}` : `/datasets/${datasetId}/knowledge`,
        {
          method: editing ? "PUT" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title, source_type: kind, content }),
        },
      );
      setNotice(
        saved.duplicate
          ? "相同内容已存在，未重复嵌入。"
          : saved.status === "ready"
            ? "知识已保存并完成索引。"
            : "知识已保存，索引未完成，请查看失败原因。 ",
      );
      clearForm();
      refresh();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "知识保存失败。");
    } finally {
      setBusy(false);
    }
  }

  async function inspect(document: KnowledgeDocument) {
    setBusy(true);
    setError("");
    try {
      const saved = await api<KnowledgeDocument>(`/knowledge/${document.id}`);
      setEditing(saved.id);
      setTitle(saved.title);
      setKind(saved.source_type);
      setContent(saved.content ?? "");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "读取失败。");
    } finally {
      setBusy(false);
    }
  }

  async function reindex(document: KnowledgeDocument) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const saved = await api<KnowledgeDocument>(
        `/knowledge/${document.id}/reindex`,
        { method: "POST" },
      );
      setNotice(
        saved.status === "ready"
          ? "重新索引完成。"
          : "索引仍未就绪，请检查失败原因。 ",
      );
      refresh();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "重新索引失败。");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!deleting || busy) return;
    setBusy(true);
    setError("");
    try {
      await api(`/knowledge/${deleting.id}`, { method: "DELETE" });
      if (editing === deleting.id) clearForm();
      setDeleting(null);
      setNotice("知识及向量已删除，新分析将不再检索该条目。");
      refresh();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "删除失败。");
    } finally {
      setBusy(false);
    }
  }

  const inputClass =
    "mt-1 block w-full rounded-md border border-slate-200 bg-white p-2 text-sm";
  return (
    <section aria-label="业务知识管理" id="business-knowledge" className="mb-7">
      <Card className="shadow-none">
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <CardTitle className="text-base">业务知识</CardTitle>
            <Button
              variant="outline"
              size="sm"
              aria-expanded={open}
              onClick={() => {
                setOpen((value) => !value);
                if (!open) setLoading(true);
              }}
            >
              {open ? "收起知识管理" : "管理业务知识"}
            </Button>
          </div>
          <p className="text-xs text-slate-500">
            补充字段别名、指标口径和业务规则；不自动向量化上传的数据行。
          </p>
        </CardHeader>
        {open && (
          <CardContent className="space-y-5">
            {error && (
              <p
                role="alert"
                className="rounded-md bg-red-50 p-3 text-sm text-red-800"
              >
                {error}
              </p>
            )}
            {notice && (
              <p
                role="status"
                className="rounded-md bg-emerald-50 p-3 text-sm text-emerald-800"
              >
                {notice}
              </p>
            )}
            <form onSubmit={save} className="space-y-3">
              <h3 className="text-sm font-medium">
                {editing ? "查看 / 编辑知识" : "新增知识"}
              </h3>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="text-sm">
                  知识标题
                  <input
                    required
                    maxLength={160}
                    value={title}
                    onChange={(event) => setTitle(event.target.value)}
                    disabled={busy}
                    className={inputClass}
                  />
                </label>
                <label className="text-sm">
                  知识类型
                  <select
                    value={kind}
                    onChange={(event) => setKind(event.target.value)}
                    disabled={busy}
                    className={inputClass}
                  >
                    {Object.entries(kinds).map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <label className="block text-sm">
                知识内容
                <textarea
                  required
                  maxLength={16000}
                  rows={4}
                  value={content}
                  onChange={(event) => setContent(event.target.value)}
                  disabled={busy}
                  placeholder={'例如：{"alias":"销售额","field":"revenue"}'}
                  className={inputClass}
                />
              </label>
              <p className="text-xs text-slate-500">
                仅填写已授权的说明与定义，不粘贴密钥、密码或敏感原始数据。索引可能调用独立配置的
                Embedding 服务。
              </p>
              <div className="flex flex-wrap gap-3">
                <Button disabled={busy || loading}>
                  {busy
                    ? "正在处理知识…"
                    : editing
                      ? "保存修改并索引"
                      : "添加并索引"}
                </Button>
                {editing && (
                  <Button
                    variant="outline"
                    type="button"
                    disabled={busy}
                    onClick={clearForm}
                  >
                    取消编辑
                  </Button>
                )}
                <Button
                  variant="outline"
                  type="button"
                  disabled={busy || loading}
                  onClick={refresh}
                >
                  刷新知识
                </Button>
              </div>
            </form>
            {loading ? (
              <p role="status" className="text-sm text-slate-500">
                正在读取知识列表…
              </p>
            ) : documents.length === 0 ? (
              <p className="text-sm text-slate-500">
                当前数据集还没有业务知识。
              </p>
            ) : (
              <ul className="space-y-3" aria-label="知识条目">
                {documents.map((document) => (
                  <li
                    key={document.id}
                    className="rounded-lg border border-slate-200 p-3 text-sm"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <p className="break-all font-medium">
                        {document.title}{" "}
                        <span className="text-xs font-normal text-slate-500">
                          {statuses[document.status]} · {document.chunk_count}{" "}
                          个片段
                        </span>
                      </p>
                      <div className="flex flex-wrap gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={busy}
                          aria-label={`查看知识 ${document.title}`}
                          onClick={() => inspect(document)}
                        >
                          查看 / 编辑
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={busy}
                          aria-label={`重新索引 ${document.title}`}
                          onClick={() => reindex(document)}
                        >
                          重新索引
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={busy}
                          aria-label={`删除知识 ${document.title}`}
                          onClick={() => setDeleting(document)}
                        >
                          删除
                        </Button>
                      </div>
                    </div>
                    {document.fake_embedding && (
                      <p className="mt-2 text-xs text-amber-800">
                        测试向量：仅用于流程演示，不代表真实语义检索质量。
                      </p>
                    )}
                    {document.error_code && (
                      <p className="mt-2 text-xs text-red-800">
                        {reasons[document.error_code] ??
                          "索引失败，可检查配置后重新索引。"}
                        （{document.error_code}）
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {deleting && (
              <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950">
                <p>
                  确认删除“{deleting.title}
                  ”？文档和向量会删除，已有分析中的引用快照按分析历史保留。
                </p>
                <div className="mt-3 flex gap-3">
                  <Button
                    variant="destructive"
                    disabled={busy}
                    onClick={remove}
                  >
                    确认删除知识
                  </Button>
                  <Button
                    variant="outline"
                    disabled={busy}
                    onClick={() => setDeleting(null)}
                  >
                    取消
                  </Button>
                </div>
              </div>
            )}
          </CardContent>
        )}
      </Card>
    </section>
  );
}
