import { test, expect } from "./fixtures";

test("knowledge CRUD and RAG citations use actual uploaded rows", async ({ page, request }) => {
  let datasetId = "";
  try {
    await page.goto("/");
    await page.getByLabel("选择数据文件").setInputFiles({
      name: "e2e-knowledge.csv", mimeType: "text/csv",
      buffer: Buffer.from("region,revenue,cost\nEast,10,1\nWest,20,2\nEast,30,3\n"),
    });
    const upload = page.waitForResponse(r => r.url().endsWith("/api/datasets") && r.request().method() === "POST");
    await page.getByRole("button", { name: "上传并分析字段" }).click();
    datasetId = (await (await upload).json()).id;
    await page.getByRole("button", { name: "管理业务知识" }).click();
    const knowledge = page.getByRole("region", { name: "业务知识管理" });
    await knowledge.getByLabel("知识标题").fill("销售额");
    await knowledge.getByLabel("知识内容").fill('{"alias":"销售额","field":"revenue"}');
    await knowledge.getByRole("button", { name: "添加并索引" }).click();
    await expect(knowledge.getByText("索引就绪", { exact: false })).toBeVisible();
    await expect(knowledge.getByText(/测试向量/)).toBeVisible();
    await knowledge.getByRole("button", { name: "查看知识 销售额" }).click();
    await expect(knowledge.getByLabel("知识内容")).toHaveValue('{"alias":"销售额","field":"revenue"}');
    await knowledge.getByRole("button", { name: "取消编辑" }).click();
    await knowledge.getByRole("button", { name: "重新索引 销售额", exact: true }).click();
    await expect(knowledge.getByText("重新索引完成。", { exact: true })).toBeVisible();
    await page.getByLabel("分析问题").fill("销售额总和");
    const completed = page.waitForResponse(r => r.url().endsWith("/analyses") && r.request().method() === "POST");
    await page.getByRole("button", { name: "执行分析" }).click();
    const analysis = await (await completed).json();
    expect(analysis.status).toBe("success");
    expect(analysis.result.rows).toEqual([{metric: 60}]);
    const sources = page.getByRole("list", { name: "本次知识来源" });
    await expect(sources).toContainText("销售额");
    await expect(sources).toContainText(analysis.telemetry.workflow.rag.citations[0].document_id);
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    await page.screenshot({ path: "test-results/knowledge-mobile.png", fullPage: true });
    await knowledge.getByRole("button", { name: "删除知识 销售额" }).click();
    await knowledge.getByRole("button", { name: "确认删除知识" }).click();
    await expect(knowledge.getByText("当前数据集还没有业务知识。", { exact: true })).toBeVisible();
    const rows = await request.get(`/api/datasets/${datasetId}/knowledge`);
    expect(await rows.json()).toEqual([]);
  } finally {
    if (datasetId) expect((await request.delete(`/api/datasets/${datasetId}`)).status()).toBe(204);
  }
});
