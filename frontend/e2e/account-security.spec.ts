import { test, expect, type BrowserContext, type Page } from "@playwright/test";
import { randomBytes } from "node:crypto";
import { createAccount } from "./fixtures";

async function login(
  context: BrowserContext,
  account: ReturnType<typeof createAccount>,
) {
  expect(
    (
      await context.request.post("/api/auth/login", {
        data: account,
        headers: { "X-DataPilot-CSRF": "1" },
      })
    ).status(),
  ).toBe(200);
}

async function openSecurity(page: Page) {
  await page.goto("/");
  await expect(page.getByText("服务已就绪")).toBeVisible();
  await page.getByRole("button", { name: "账户安全", exact: true }).click();
  await expect(page.getByRole("heading", { name: "登录会话" })).toBeVisible();
  await expect(page.getByRole("button", { name: "刷新会话" })).toBeEnabled();
}

test("password change validates input, revokes both real sessions and accepts only the new password", async ({
  page,
  context,
  browser,
  baseURL,
}) => {
  test.setTimeout(60_000);
  const account = createAccount();
  const changedPassword = randomBytes(24).toString("base64url");
  const other = await browser.newContext({ baseURL });
  try {
    await login(context, account);
    await login(other, account);
    await openSecurity(page);
    await expect(page.getByRole("listitem")).toHaveCount(2);
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(390);
    await page.screenshot({
      path: "test-results/account-security-mobile.png",
      fullPage: true,
    });
    // Returning to the workbench and opening the panel clears password state.
    await page.getByLabel("当前密码", { exact: true }).fill(account.password);
    await page.getByRole("button", { name: "返回工作台" }).click();
    await page.getByRole("button", { name: "账户安全", exact: true }).click();
    await expect(page.getByLabel("当前密码", { exact: true })).toHaveValue("");

    let passwordPosts = 0;
    page.on("request", (request) => {
      if (
        request.url().endsWith("/api/auth/password") &&
        request.method() === "POST"
      )
        passwordPosts++;
    });
    await page.getByLabel("当前密码", { exact: true }).fill(account.password);
    await page
      .getByLabel("新密码（12–128 个字符）", { exact: true })
      .fill(changedPassword);
    await page
      .getByLabel("确认新密码", { exact: true })
      .fill("different-confirmation");
    await page.getByRole("button", { name: "修改密码并退出全部会话" }).click();
    await expect(
      page.getByRole("alert").filter({ hasText: "两次输入的新密码不一致" }),
    ).toBeVisible();
    expect(passwordPosts).toBe(0);

    await page.getByLabel("确认新密码", { exact: true }).fill(changedPassword);
    await page
      .getByLabel("当前密码", { exact: true })
      .fill("incorrect-current-password");
    const failed = page.waitForResponse((response) =>
      response.url().endsWith("/api/auth/password"),
    );
    await page.getByRole("button", { name: "修改密码并退出全部会话" }).click();
    expect((await failed).status()).toBe(403);
    await expect(
      page.getByRole("alert").filter({ hasText: "当前密码错误" }),
    ).toBeVisible();
    expect((await other.request.get("/api/auth/me")).status()).toBe(200);

    await page.getByLabel("当前密码", { exact: true }).fill(account.password);
    await page.getByRole("button", { name: "修改密码并退出全部会话" }).click();
    await expect(page.getByRole("status")).toContainText("密码已修改");
    await expect(
      page.getByRole("button", { name: "登录", exact: true }),
    ).toBeVisible();
    expect((await context.request.get("/api/auth/me")).status()).toBe(401);
    expect((await other.request.get("/api/auth/me")).status()).toBe(401);
    await page.getByLabel("用户名", { exact: true }).fill(account.username);
    await page.getByLabel("密码", { exact: true }).fill(account.password);
    await page.getByRole("button", { name: "登录", exact: true }).click();
    await expect(
      page.getByRole("alert").filter({ hasText: "用户名或密码错误" }),
    ).toBeVisible();
    await page.getByLabel("密码", { exact: true }).fill(changedPassword);
    await page.getByRole("button", { name: "登录", exact: true }).click();
    await expect(
      page.getByText(`当前用户：${account.username}`, { exact: true }),
    ).toBeVisible();
    await expect(page.getByText("服务已就绪")).toBeVisible();
    expect(passwordPosts).toBe(2);
  } finally {
    await context.request.post("/api/auth/logout", {
      headers: { "X-DataPilot-CSRF": "1" },
    });
    await other.close();
  }
});

test("session controls revoke another session, all sessions and the current session through real APIs", async ({
  page,
  context,
  browser,
  baseURL,
}) => {
  test.setTimeout(60_000);
  const account = createAccount();
  const other = await browser.newContext({ baseURL });
  try {
    await login(context, account);
    await login(other, account);
    const sessions = await (
      await context.request.get("/api/auth/sessions")
    ).json();
    const otherSession = sessions.find(
      (session: { current: boolean }) => !session.current,
    );
    expect(otherSession).toBeTruthy();
    await openSecurity(page);
    await page
      .getByRole("button", {
        name: `撤销会话 ${otherSession.id.slice(0, 8)}`,
        exact: true,
      })
      .click();
    await page.getByRole("button", { name: "确认撤销", exact: true }).click();
    await expect(
      page.getByRole("alert").filter({ hasText: "请先输入当前密码" }),
    ).toBeVisible();
    await page.getByLabel("当前密码", { exact: true }).fill(account.password);
    await page.getByRole("button", { name: "确认撤销", exact: true }).click();
    await expect(page.getByRole("status")).toContainText("所选会话已撤销");
    await expect(page.getByRole("listitem")).toHaveCount(1);
    await expect(page.getByLabel("当前密码", { exact: true })).toHaveValue("");
    expect((await other.request.get("/api/auth/me")).status()).toBe(401);
    expect((await context.request.get("/api/auth/me")).status()).toBe(200);

    await login(other, account);
    await page.getByRole("button", { name: "刷新会话" }).click();
    await expect(page.getByRole("listitem")).toHaveCount(2);
    await page.getByLabel("当前密码", { exact: true }).fill(account.password);
    await page
      .getByRole("button", { name: "退出全部会话", exact: true })
      .click();
    await page
      .getByRole("button", { name: "确认退出全部", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "登录", exact: true }),
    ).toBeVisible();
    await expect(page.getByRole("status")).toContainText("所有会话已退出");
    expect((await context.request.get("/api/auth/me")).status()).toBe(401);
    expect((await other.request.get("/api/auth/me")).status()).toBe(401);

    await login(context, account);
    await openSecurity(page);
    await page.getByLabel("当前密码", { exact: true }).fill(account.password);
    await page
      .getByRole("button", { name: "撤销当前会话", exact: true })
      .click();
    await page.getByRole("button", { name: "确认撤销", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "登录", exact: true }),
    ).toBeVisible();
    await expect(page.getByRole("status")).toContainText("当前会话已撤销");
    expect((await context.request.get("/api/auth/me")).status()).toBe(401);
  } finally {
    await context.request.post("/api/auth/logout", {
      headers: { "X-DataPilot-CSRF": "1" },
    });
    await other.close();
  }
});
