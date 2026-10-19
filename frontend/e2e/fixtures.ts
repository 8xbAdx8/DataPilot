import { test as base, expect } from "@playwright/test";
import { randomBytes } from "node:crypto";
import { spawnSync } from "node:child_process";
import path from "node:path";

export function createAccount() {
  const username = `e2e-${randomBytes(8).toString("hex")}`;
  const password = randomBytes(24).toString("base64url");
  const root = path.resolve(process.cwd(), "..");
  const python =
    process.platform === "win32"
      ? path.join(root, ".venv/Scripts/python.exe")
      : "python";
  const result = spawnSync(
    python,
    [
      "-m",
      "app.accounts",
      "create-user",
      "--username",
      username,
      "--password-stdin",
    ],
    {
      cwd: root,
      input: password + "\n",
      encoding: "utf8",
      timeout: 30_000,
    },
  );
  if (result.status !== 0)
    throw new Error(`Test account provisioning failed: ${result.stderr}`);
  return { username, password };
}

export const test = base.extend<
  object,
  { account: ReturnType<typeof createAccount> }
>({
  account: [
    async ({}, provide) => {
      await provide(createAccount());
    },
    { scope: "worker" },
  ],
  context: async ({ context, account }, provide) => {
    const response = await context.request.post("/api/auth/login", {
      data: account,
      headers: { "X-DataPilot-CSRF": "1" },
    });
    expect(response.status()).toBe(200);
    await provide(context);
    await context.request.post("/api/auth/logout", {
      headers: { "X-DataPilot-CSRF": "1" },
    });
  },
  request: async ({ context }, provide) => {
    await provide(context.request);
  },
});

export { expect };
