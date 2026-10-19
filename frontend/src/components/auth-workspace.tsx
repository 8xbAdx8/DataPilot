"use client";

import { useEffect, useState, type FormEvent } from "react";
import { api, ApiError, type User } from "@/lib/api";
import { Workbench } from "@/components/workbench";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export function AuthWorkspace() {
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(true);
  const [busy, setBusy] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    api<User>("/auth/me")
      .then((value) => {
        if (active) setUser(value);
      })
      .catch((e) => {
        if (
          active &&
          !(e instanceof ApiError && e.code === "authentication_required")
        )
          setError(e.message);
      })
      .finally(() => {
        if (active) setChecking(false);
      });
    function expired() {
      setUser(null);
      setPassword("");
      setError("会话已失效，请重新登录。");
    }
    window.addEventListener("datapilot-session-expired", expired);
    return () => {
      active = false;
      window.removeEventListener("datapilot-session-expired", expired);
    };
  }, []);

  async function login(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      setUser(
        await api<User>("/auth/login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username, password }),
        }),
      );
      setPassword("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "登录失败。");
    } finally {
      setBusy(false);
    }
  }

  async function logout() {
    setBusy(true);
    setError("");
    try {
      await api("/auth/logout", { method: "POST" });
      setUser(null);
      setPassword("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "退出失败，请重试。");
    } finally {
      setBusy(false);
    }
  }

  if (checking)
    return (
      <p role="status" className="p-10 text-center text-slate-500">
        正在检查登录状态…
      </p>
    );
  if (user)
    return (
      <>
        <div className="flex flex-wrap items-center justify-end gap-4 border-b bg-white px-6 py-3 text-sm text-slate-600">
          <span>当前用户：{user.username}</span>
          <Button variant="outline" size="sm" disabled={busy} onClick={logout}>
            退出登录
          </Button>
          {error && (
            <p role="alert" className="text-red-800">
              {error}
            </p>
          )}
        </div>
        <Workbench key={user.id} />
      </>
    );
  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-50 p-5">
      <Card className="w-full max-w-md">
        <CardHeader>
          <p className="mb-3 font-semibold text-emerald-700">DataPilot ↗</p>
          <CardTitle>登录数据分析工作台</CardTitle>
          <p className="text-sm text-slate-500">
            使用你的账户访问数据集和分析历史。
          </p>
        </CardHeader>
        <CardContent>
          <form onSubmit={login} className="space-y-5">
            <label className="block text-sm font-medium">
              用户名
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                required
                minLength={3}
                maxLength={64}
                disabled={busy}
                className="mt-2 block w-full rounded-md border border-slate-200 p-3"
              />
            </label>
            <label className="block text-sm font-medium">
              密码
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
                maxLength={128}
                disabled={busy}
                className="mt-2 block w-full rounded-md border border-slate-200 p-3"
              />
            </label>
            {error && (
              <p
                role="alert"
                className="rounded-md bg-red-50 p-3 text-sm text-red-800"
              >
                {error}
              </p>
            )}
            <Button className="w-full" disabled={busy}>
              {busy ? "正在登录…" : "登录"}
            </Button>
          </form>
          <p className="mt-5 text-xs text-slate-500">
            还没有账户？请联系管理员。
          </p>
        </CardContent>
      </Card>
    </main>
  );
}
