"use client";

import { useEffect, useState, type FormEvent } from "react";
import { api, ApiError, type AuthSession } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

function dateLabel(value: string | null) {
  if (!value) return "未知";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "未知" : date.toLocaleString("zh-CN");
}

function errorMessage(error: unknown) {
  const message = error instanceof Error ? error.message : "操作失败，请重试。";
  return error instanceof ApiError && error.retryAfter
    ? `${message} 请在 ${error.retryAfter} 秒后重试。`
    : message;
}

export function AccountSecurity({
  onClose,
  onSignedOut,
}: {
  onClose: () => void;
  onSignedOut: (message: string) => void;
}) {
  const [sessions, setSessions] = useState<AuthSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<"password" | "revoke" | "all" | null>(null);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [target, setTarget] = useState<AuthSession | "all" | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let active = true;
    api<AuthSession[]>("/auth/sessions")
      .then((value) => {
        if (active) setSessions(value);
      })
      .catch((error) => {
        if (active) setError(errorMessage(error));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  async function refresh() {
    if (busy || loading) return;
    setLoading(true);
    setError("");
    try {
      setSessions(await api<AuthSession[]>("/auth/sessions"));
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setLoading(false);
    }
  }

  function hasCurrentPassword() {
    if (currentPassword) return true;
    setError("请先输入当前密码，验证账户后再操作。");
    return false;
  }

  async function changePassword(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setError("");
    setNotice("");
    if (!hasCurrentPassword()) return;
    if (newPassword !== confirmation) {
      setError("两次输入的新密码不一致，请重新确认。");
      return;
    }
    if (newPassword.length < 12 || newPassword.length > 128) {
      setError("新密码必须为 12–128 个字符。");
      return;
    }
    if (newPassword === currentPassword) {
      setError("新密码须与当前密码不同。");
      return;
    }
    setBusy("password");
    try {
      await api<void>("/auth/password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });
      onSignedOut("密码已修改，所有会话已退出。请使用新密码重新登录。");
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function revoke() {
    if (busy || !target) return;
    setError("");
    setNotice("");
    if (!hasCurrentPassword()) return;
    const selected = target;
    setBusy(selected === "all" ? "all" : "revoke");
    try {
      await api<void>(
        selected === "all"
          ? "/auth/logout-all"
          : `/auth/sessions/${selected.id}`,
        {
          method: selected === "all" ? "POST" : "DELETE",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ current_password: currentPassword }),
        },
      );
      setCurrentPassword("");
      setTarget(null);
      if (selected === "all" || selected.current) {
        onSignedOut(
          selected === "all"
            ? "所有会话已退出，请重新登录。"
            : "当前会话已撤销，请重新登录。",
        );
      } else {
        setSessions((items) => items.filter((item) => item.id !== selected.id));
        setNotice("所选会话已撤销，该会话需要重新登录。");
      }
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  const disabled = busy !== null;
  const inputClass =
    "mt-2 block w-full rounded-md border border-slate-200 bg-white p-3";

  return (
    <main className="min-h-screen bg-slate-50 p-5 sm:p-10">
      <div className="mx-auto max-w-4xl space-y-6">
        <header className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold">账户安全</h1>
            <p className="mt-2 text-sm text-slate-600">
              修改密码、撤销会话或退出全部设备时，需验证当前密码。
            </p>
          </div>
          <Button variant="outline" disabled={disabled} onClick={onClose}>
            返回工作台
          </Button>
        </header>
        {error && (
          <p
            role="alert"
            className="rounded-md bg-red-50 p-4 text-sm text-red-800"
          >
            {error}
          </p>
        )}
        {notice && (
          <p
            role="status"
            className="rounded-md bg-emerald-50 p-4 text-sm text-emerald-800"
          >
            {notice}
          </p>
        )}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">验证账户</CardTitle>
          </CardHeader>
          <CardContent>
            <label className="block max-w-md text-sm font-medium">
              当前密码
              <input
                type="password"
                autoComplete="current-password"
                value={currentPassword}
                maxLength={128}
                disabled={disabled}
                onChange={(event) => setCurrentPassword(event.target.value)}
                className={inputClass}
              />
            </label>
            <p className="mt-3 text-xs text-slate-500">
              关闭此面板会清除输入的密码。密码不会保存在浏览器存储中。
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">修改密码</CardTitle>
            <p className="text-sm text-slate-500">
              修改成功后，全部设备的会话立即失效，包括当前会话。
            </p>
          </CardHeader>
          <CardContent>
            <form onSubmit={changePassword} className="max-w-md space-y-4">
              <label className="block text-sm font-medium">
                新密码（12–128 个字符）
                <input
                  type="password"
                  autoComplete="new-password"
                  required
                  minLength={12}
                  maxLength={128}
                  disabled={disabled}
                  value={newPassword}
                  onChange={(event) => setNewPassword(event.target.value)}
                  className={inputClass}
                />
              </label>
              <label className="block text-sm font-medium">
                确认新密码
                <input
                  type="password"
                  autoComplete="new-password"
                  required
                  minLength={12}
                  maxLength={128}
                  disabled={disabled}
                  value={confirmation}
                  onChange={(event) => setConfirmation(event.target.value)}
                  className={inputClass}
                />
              </label>
              <Button type="submit" disabled={disabled}>
                {busy === "password" ? "正在修改…" : "修改密码并退出全部会话"}
              </Button>
            </form>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <CardTitle className="text-base">登录会话</CardTitle>
              <Button
                size="sm"
                variant="outline"
                onClick={refresh}
                disabled={disabled || loading}
              >
                {loading ? "正在加载…" : "刷新会话"}
              </Button>
            </div>
            <p className="text-sm text-slate-500">
              仅展示当前账户尚未过期的会话，时间按本机时区显示。
            </p>
          </CardHeader>
          <CardContent className="space-y-4">
            {loading ? (
              <p role="status" className="text-sm text-slate-500">
                正在读取登录会话…
              </p>
            ) : sessions.length ? (
              <ul className="divide-y divide-slate-200 rounded-lg border border-slate-200">
                {sessions.map((session) => (
                  <li key={session.id} className="p-4">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div className="min-w-0 text-sm">
                        <p className="font-medium">
                          会话 {session.id.slice(0, 8)}
                          {session.current && (
                            <span className="ml-3 rounded-full bg-emerald-50 px-2 py-1 text-xs text-emerald-700">
                              当前会话
                            </span>
                          )}
                        </p>
                        <p className="mt-2 text-xs text-slate-500">
                          创建时间：{dateLabel(session.created_at)}
                        </p>
                        <p className="mt-1 text-xs text-slate-500">
                          到期时间：{dateLabel(session.expires_at)}
                        </p>
                      </div>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={disabled}
                        aria-label={
                          session.current
                            ? "撤销当前会话"
                            : `撤销会话 ${session.id.slice(0, 8)}`
                        }
                        onClick={() => {
                          setTarget(session);
                          setError("");
                          setNotice("");
                        }}
                      >
                        撤销会话
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-slate-500">没有可显示的会话。</p>
            )}
            <Button
              variant="outline"
              disabled={disabled || loading}
              onClick={() => {
                setTarget("all");
                setError("");
                setNotice("");
              }}
            >
              退出全部会话
            </Button>
            {target && (
              <div className="space-y-3 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950">
                <p>
                  {target === "all"
                    ? "将退出全部设备，包括当前会话。你需要重新登录。"
                    : target.current
                      ? "将撤销当前会话，并返回登录页面。"
                      : `将撤销会话 ${target.id.slice(0, 8)}，该会话需要重新登录。`}
                </p>
                <div className="flex flex-wrap gap-3">
                  <Button
                    variant="destructive"
                    size="sm"
                    disabled={disabled}
                    onClick={revoke}
                  >
                    {busy === "all" || busy === "revoke"
                      ? "正在撤销…"
                      : target === "all"
                        ? "确认退出全部"
                        : "确认撤销"}
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={disabled}
                    onClick={() => setTarget(null)}
                  >
                    取消
                  </Button>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </main>
  );
}
