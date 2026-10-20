import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { test, expect } from "./fixtures";

test("real semantic RAG reaches the production browser, SQL result and citations", async ({ page, request }) => {
  test.skip(process.env.RUN_LIVE_BROWSER_RAG !== "1", "Real providers require RUN_LIVE_BROWSER_RAG=1");
  test.setTimeout(120_000);
  const config = await (await request.get("/api/config")).json();
  expect(config.provider).not.toBe("mock");
  expect(config.embedding.provider).toBe("openai_compatible");
  let datasetId = "";
  try {
    await page.goto("/");
    await page.getByLabel("选择数据文件").setInputFiles({
      name: "synthetic-live-sales.csv", mimeType: "text/csv",
      buffer: Buffer.from("region,revenue,cost,status\nEast,10,4,paid\nWest,20,8,paid\nEast,30,12,refund\n"),
    });
    const uploaded = page.waitForResponse(r => r.url().endsWith("/api/datasets") && r.request().method() === "POST");
    await page.getByRole("button", { name: "上传并分析字段" }).click();
    datasetId = (await (await uploaded).json()).id;
    await page.getByRole("button", { name: "管理业务知识" }).click();
    const knowledge = page.getByRole("region", { name: "业务知识管理" });
    await knowledge.getByLabel("知识标题").fill("净贡献额");
    await knowledge.getByLabel("知识类型").selectOption("metric_definition");
    await knowledge.getByLabel("知识内容").fill(
      "净贡献额：仅统计 status='paid' 的订单，计算 SUM(revenue)-SUM(cost)。退款订单不计入。",
    );
    await knowledge.getByRole("button", { name: "添加并索引" }).click();
    await expect(knowledge.getByText("索引就绪", { exact: false })).toBeVisible();
    await expect(knowledge.getByText(/测试向量/)).toHaveCount(0);
    const question = "根据业务定义按 region 统计净贡献额，输出 region 和 net_contribution 两列，按 region 升序。先检查 Schema，使用知识口径。";
    await page.getByLabel("分析问题").fill(question);
    const completed = page.waitForResponse(r => r.url().endsWith("/analyses") && r.request().method() === "POST");
    await page.getByRole("button", { name: "执行分析" }).click();
    const response = await completed;
    expect(response.status()).toBe(201);
    const analysis = await response.json();
    expect(analysis.status).toBe("success");
    expect(analysis.result.rows).toEqual([
      { region: "East", net_contribution: 6 },
      { region: "West", net_contribution: 12 },
    ]);
    const workflow = analysis.telemetry.workflow;
    expect(workflow.rag.used).toBe(true);
    expect(workflow.rag.source).toBe("provider");
    expect(workflow.agent.mode).toBe("native_tools");
    expect(workflow.plan.knowledge_chunk_ids.length).toBeGreaterThan(0);
    await expect(page.getByLabel("SQL")).toContainText("SUM");
    const sources = page.getByRole("list", { name: "本次知识来源" });
    await expect(sources).toContainText("净贡献额");
    for (const source of workflow.rag.citations) {
      await expect(sources).toContainText(source.document_id);
      const saved = await request.get(`/api/knowledge/${source.document_id}`);
      expect(saved.status()).toBe(200);
      expect((await saved.json()).content).toContain("SUM(revenue)-SUM(cost)");
    }
    await expect(page.getByRole("img", { name: /查询结果柱状图/ })).toBeVisible();
    const downloaded = page.waitForEvent("download");
    await page.getByRole("link", { name: "导出结果 CSV" }).click();
    const csv = await readFile((await (await downloaded).path())!, "utf8");
    expect(csv).toContain("East,6");
    expect(csv).toContain("West,12");
    await page.reload();
    await page.getByRole("region", { name: "分析历史", exact: true })
      .getByRole("button", { name: new RegExp("根据业务定义按 region 统计净贡献额") }).click();
    await expect(page.getByRole("list", { name: "本次知识来源" })).toContainText("净贡献额");
    await expect(page.getByRole("img", { name: /查询结果柱状图/ })).toBeVisible();
    const output = path.resolve(process.cwd(), "../data/runtime");
    await mkdir(output, { recursive: true });
    await page.screenshot({ path: path.join(output, "phase10-real-browser.png"), fullPage: true });
    await writeFile(path.join(output, "phase10-real-browser.json"), JSON.stringify({
      status: "passed", provider: analysis.provider, model: analysis.model,
      sql: analysis.sql, rows: analysis.result.rows, chart: analysis.result.chart,
      telemetry: analysis.telemetry, history_and_csv: "passed",
    }, null, 2));
  } finally {
    if (datasetId) expect((await request.delete(`/api/datasets/${datasetId}`)).status()).toBe(204);
  }
});
