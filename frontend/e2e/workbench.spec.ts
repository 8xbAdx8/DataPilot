import { test, expect } from "./fixtures";
import { readFile } from "node:fs/promises";

test.afterEach(async ({ request }) => {
  const response = await request.get("/api/datasets");
  const datasets = await response.json();
  for (const dataset of datasets.filter((d: { name: string }) =>
    d.name.startsWith("e2e-"),
  )) {
    expect((await request.delete(`/api/datasets/${dataset.id}`)).status()).toBe(
      204,
    );
  }
});

test("uploads a real CSV and renders persisted profile and preview", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByText("服务已就绪")).toBeVisible();
  await page.getByLabel("选择数据文件").setInputFiles({
    name: "e2e-sales.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("region,revenue\nEast,10\nWest,\nEast,30\n"),
  });
  await page.getByRole("button", { name: "上传并分析字段" }).click();
  await expect(
    page.getByRole("button", { name: /e2e-sales.csv/ }).first(),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "字段与数据质量" }),
  ).toBeVisible();
  await expect(
    page.getByRole("row").filter({ hasText: "revenue" }).first(),
  ).toContainText("33.3%");
  await page.reload();
  await expect(
    page.getByRole("button", { name: /e2e-sales.csv/ }).first(),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/workbench-desktop.png",
    fullPage: true,
  });
});

test("shows validation errors and can recover by uploading a valid file", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("选择数据文件").setInputFiles({
    name: "duplicate.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("a,a\n1,2\n"),
  });
  await page.getByRole("button", { name: "上传并分析字段" }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "重复列名" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "上传并分析字段" }),
  ).toBeEnabled();
  await page.getByLabel("选择数据文件").setInputFiles({
    name: "e2e-recovered.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("a,b\n1,2\n"),
  });
  await page.getByRole("button", { name: "上传并分析字段" }).click();
  await expect(
    page.getByRole("button", { name: /e2e-recovered.csv/ }).first(),
  ).toBeVisible();
  await expect(
    page.getByRole("alert").filter({ hasText: "重复列名" }),
  ).toHaveCount(0);
});

test("mobile layout remains usable", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByLabel("选择数据文件")).toBeVisible();
  await expect(page.getByText("服务已就绪")).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.screenshot({
    path: "test-results/workbench-mobile.png",
    fullPage: true,
  });
});

test("Mock analysis runs real SQL, renders chart, restores history and exports actual rows", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await page.getByLabel("选择数据文件").setInputFiles({
    name: "e2e-analysis.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("region,revenue\nEast,10\nWest,20\nEast,30\n"),
  });
  const uploaded = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/datasets") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "上传并分析字段" }).click();
  expect((await uploaded).status()).toBe(201);
  await expect(
    page.getByRole("button", { name: "上传并分析字段" }),
  ).toBeEnabled();
  await page.getByLabel("分析问题").fill("按 region 统计 revenue 总和");
  const responsePromise = page.waitForResponse(
    (response) =>
      response.url().endsWith("/analyses") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "执行分析" }).click();
  const response = await responsePromise;
  expect(response.status()).toBe(201);
  const analysis = await response.json();
  expect(analysis.request_id).toBe(response.headers()["x-request-id"]);
  expect(analysis.result.rows).toEqual([
    { region: "East", metric: 40 },
    { region: "West", metric: 20 },
  ]);
  await expect(page.getByLabel("SQL")).toContainText("SUM");
  await expect(page.getByRole("img", { name: /查询结果柱状图/ })).toBeVisible();
  await expect(
    page.getByRole("img", { name: /查询结果柱状图/ }).locator("canvas"),
  ).toHaveCount(1);
  await expect(page.getByText("实际返回 2 行", { exact: true })).toBeVisible();
  await page.getByText("执行详情", { exact: true }).click();
  await expect(page.getByLabel("执行诊断")).toContainText(analysis.request_id);
  await expect(page.getByLabel("执行诊断")).toContainText("查询执行");
  await expect(page.getByLabel("执行诊断")).toContainText(
    "Mock 模板未调用模型 API（Token 0）",
  );
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("link", { name: "导出结果 CSV" }).click();
  const download = await downloadPromise;
  const contents = await readFile((await download.path())!, "utf8");
  expect(contents).toContain("East,40");
  expect(contents).toContain("West,20");
  await page.screenshot({
    path: "test-results/analysis-desktop.png",
    fullPage: true,
  });
  await page.reload();
  await page
    .getByRole("region", { name: "分析历史", exact: true })
    .getByRole("button", { name: /按 region 统计 revenue 总和/ })
    .click();
  await expect(page.getByLabel("SQL")).toContainText("SUM");
  await expect(page.getByText("实际返回 2 行", { exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});

test("dataset deletion is explicit and removes the selected dataset", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("选择数据文件").setInputFiles({
    name: "e2e-delete.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("a,b\n1,2\n"),
  });
  await page.getByRole("button", { name: "上传并分析字段" }).click();
  await expect(
    page.getByRole("button", { name: /e2e-delete.csv/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: "删除当前数据集" }).click();
  await page.getByRole("button", { name: "确认删除", exact: true }).click();
  await expect(
    page.getByRole("button", { name: /e2e-delete.csv/ }),
  ).toHaveCount(0);
});
