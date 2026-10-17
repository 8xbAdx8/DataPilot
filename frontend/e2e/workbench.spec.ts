import { test, expect } from "@playwright/test";

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
