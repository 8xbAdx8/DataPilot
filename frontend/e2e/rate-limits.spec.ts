import { test, expect } from "@playwright/test";
import { createAccount } from "./fixtures";

test("real analysis budget returns retry guidance and the browser does not auto-retry", async ({
  page,
  context,
}) => {
  test.setTimeout(90_000);
  const account = createAccount();
  const login = await context.request.post("/api/auth/login", {
    data: account,
  });
  expect(login.status()).toBe(200);
  const config = await (await context.request.get("/api/config")).json();
  const uploaded = await context.request.post("/api/datasets", {
    multipart: {
      file: {
        name: "e2e-quota.csv",
        mimeType: "text/csv",
        buffer: Buffer.from("a,b\n1,2\n"),
      },
    },
  });
  expect(uploaded.status()).toBe(201);
  const dataset = await uploaded.json();
  try {
    // Real Mock failures consume capacity without creating fake query results.
    for (let i = 0; i < config.limits.analyses_per_window; i++) {
      const response = await context.request.post(
        `/api/datasets/${dataset.id}/analyses`,
        { data: { question: "不支持的问题" } },
      );
      expect(response.status()).toBe(422);
    }
    await page.goto("/");
    await expect(page.getByText("服务已就绪")).toBeVisible();
    await page.getByLabel("分析问题").fill("行数");
    let posts = 0;
    page.on("request", (request) => {
      if (request.url().endsWith("/analyses") && request.method() === "POST")
        posts++;
    });
    const responsePromise = page.waitForResponse(
      (response) =>
        response.url().endsWith("/analyses") &&
        response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "执行分析" }).click();
    const response = await responsePromise;
    expect(response.status()).toBe(429);
    expect(Number(response.headers()["retry-after"])).toBeGreaterThan(0);
    expect(Number(response.headers()["retry-after"])).toBeLessThanOrEqual(
      config.limits.window_seconds,
    );
    const alert = page.getByRole("alert").filter({ hasText: "请求过于频繁" });
    await expect(alert).toContainText("秒后重试");
    await expect(alert).toContainText(response.headers()["x-request-id"]);
    await expect(
      page.getByRole("heading", { name: "分析结果", exact: true }),
    ).toHaveCount(0);
    await expect(page.getByRole("button", { name: "执行分析" })).toBeEnabled();
    expect(posts).toBe(1);
    await alert.screenshot({ path: "test-results/rate-limit-feedback.png" });
  } finally {
    expect(
      (await context.request.delete(`/api/datasets/${dataset.id}`)).status(),
    ).toBe(204);
    await context.request.post("/api/auth/logout");
  }
});
