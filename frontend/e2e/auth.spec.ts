import { test, expect } from "@playwright/test";
import { createAccount } from "./fixtures";

test("real UI login, logout and two-user isolation cover datasets, history and exports", async ({
  page,
  context,
}) => {
  test.setTimeout(60_000);
  const owner = createAccount();
  const other = createAccount();
  let datasetId = "";
  try {
    await page.goto("/");
    await page.getByLabel("用户名", { exact: true }).fill(owner.username);
    await page.getByLabel("密码", { exact: true }).fill("incorrect-password");
    await page.getByRole("button", { name: "登录", exact: true }).click();
    await expect(
      page.getByRole("alert").filter({ hasText: "用户名或密码错误" }),
    ).toBeVisible();
    await page.getByLabel("密码", { exact: true }).fill(owner.password);
    await page.getByRole("button", { name: "登录", exact: true }).click();
    await expect(
      page.getByText(`当前用户：${owner.username}`, { exact: true }),
    ).toBeVisible();
    await expect(page.getByText("服务已就绪")).toBeVisible();
    const cookie = (await context.cookies()).find(
      (item) => item.name === "datapilot_session",
    )!;
    expect(cookie.httpOnly).toBe(true);
    expect(cookie.sameSite).toBe("Strict");
    expect(await page.evaluate(() => document.cookie)).not.toContain(
      "datapilot_session",
    );
    await page.getByLabel("选择数据文件").setInputFiles({
      name: "e2e-private-owned.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("region,revenue\nEast,10\nWest,20\n"),
    });
    const uploaded = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/datasets") && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "上传并分析字段" }).click();
    datasetId = (await (await uploaded).json()).id;
    await expect(page.getByLabel("分析问题")).toBeVisible();
    await page.getByLabel("分析问题").fill("行数");
    const analyzed = page.waitForResponse(
      (r) => r.url().endsWith("/analyses") && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "执行分析" }).click();
    const analysis = await (await analyzed).json();
    expect(analysis.result.rows).toEqual([{ row_count: 2 }]);
    await page.reload();
    await expect(
      page.getByText(`当前用户：${owner.username}`, { exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "退出登录" }).click();
    await expect(
      page.getByRole("button", { name: "登录", exact: true }),
    ).toBeVisible();
    expect((await context.request.get("/api/datasets")).status()).toBe(401);
    await page.getByLabel("用户名", { exact: true }).fill(other.username);
    await page.getByLabel("密码", { exact: true }).fill(other.password);
    await page.getByRole("button", { name: "登录", exact: true }).click();
    await expect(
      page.getByText(`当前用户：${other.username}`, { exact: true }),
    ).toBeVisible();
    await expect(page.getByText("服务已就绪")).toBeVisible();
    await expect(
      page.getByRole("button", { name: /e2e-private-owned/ }),
    ).toHaveCount(0);
    for (const path of [
      `/datasets/${datasetId}`,
      `/analyses/${analysis.id}`,
      `/analyses/${analysis.id}/export`,
    ]) {
      expect((await context.request.get(`/api${path}`)).status()).toBe(404);
    }
    expect(
      (await context.request.delete(`/api/datasets/${datasetId}`)).status(),
    ).toBe(404);
    expect(
      (
        await context.request.post(`/api/datasets/${datasetId}/analyses`, {
          data: { question: "行数" },
        })
      ).status(),
    ).toBe(404);
    expect(
      await (
        await context.request.get(`/api/analyses?dataset_id=${datasetId}`)
      ).json(),
    ).toEqual([]);
    await context.clearCookies();
    await page.reload();
    await expect(
      page.getByRole("button", { name: "登录", exact: true }),
    ).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(390);
    await page.screenshot({ path: "test-results/login-mobile.png" });
  } finally {
    // The owner, never a global cleanup identity, removes this test's upload.
    expect(
      (await context.request.post("/api/auth/login", { data: owner })).status(),
    ).toBe(200);
    if (datasetId)
      expect(
        (await context.request.delete(`/api/datasets/${datasetId}`)).status(),
      ).toBe(204);
    await context.request.post("/api/auth/logout");
  }
});
