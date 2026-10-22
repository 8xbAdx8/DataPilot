import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { test, expect } from "./fixtures";

test("stage19 separate exact-name tool diagnosis preserves the initial failure", async ({
  page,
  request,
}) => {
  test.skip(
    process.env.RUN_PHASE19_DIAGNOSTIC !== "1",
    "Real providers require RUN_PHASE19_DIAGNOSTIC=1",
  );
  test.setTimeout(120_000);
  const previous = JSON.parse(
    await readFile(
      path.resolve(process.cwd(), "../data/runtime/phase19-live-browser.json"),
      "utf8",
    ),
  );
  expect(previous.unknown_usage).toBe(false);
  expect(previous.total_chat_tokens + 70_000).toBeLessThanOrEqual(90_000);
  await writeFile(
    path.resolve(
      process.cwd(),
      "../data/runtime/phase19-exact-name-diagnostic.claim.json",
    ),
    JSON.stringify({
      claimed: true,
      prior_chat_tokens: previous.total_chat_tokens,
    }),
    { flag: "wx" },
  );
  const config = await (await request.get("/api/config")).json();
  expect(config.provider).not.toBe("mock");
  expect(config.embedding.provider).toBe("openai_compatible");
  let datasetId = "";
  try {
    await page.goto("/");
    await page.getByLabel("选择数据文件").setInputFiles({
      name: "synthetic-live-sales.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(
        "region,revenue,cost,status\nEast,10,4,paid\nWest,20,8,paid\nEast,30,12,refund\n",
      ),
    });
    const uploaded = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/datasets") && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "上传并分析字段" }).click();
    datasetId = (await (await uploaded).json()).id;
    await page.getByRole("button", { name: "管理业务知识" }).click();
    const knowledge = page.getByRole("region", { name: "业务知识管理" });
    await knowledge.getByLabel("知识标题").fill("净贡献额");
    await knowledge.getByLabel("知识类型").selectOption("metric_definition");
    await knowledge
      .getByLabel("知识内容")
      .fill(
        "净贡献额：仅统计 status='paid' 的订单，计算 SUM(revenue)-SUM(cost)。退款订单不计入。",
      );
    await knowledge.getByRole("button", { name: "添加并索引" }).click();
    await expect(
      knowledge.getByText("索引就绪", { exact: false }),
    ).toBeVisible();
    await expect(knowledge.getByText(/测试向量/)).toHaveCount(0);
    const question =
      "先检查Schema，并调用get_metric_definition按精确名称“净贡献额”取得已登记定义，再按region分组分析该指标。仅输出region和net_contribution两列，按region升序；缺少定义必须澄清，不能自行定义公式。";
    await page.getByLabel("分析问题").fill(question);
    const completed = page.waitForResponse(
      (r) => r.url().endsWith("/analyses") && r.request().method() === "POST",
    );
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
    expect(typeof analysis.telemetry.usage.total_tokens).toBe("number");
    expect(typeof workflow.agent.usage.total_tokens).toBe("number");
    expect(workflow.rag.used).toBe(true);
    expect(workflow.agent.tools_used).toContain("get_metric_definition");
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
    await expect(
      page.getByRole("img", { name: /查询结果柱状图/ }),
    ).toBeVisible();
    const downloaded = page.waitForEvent("download");
    await page.getByRole("link", { name: "导出结果 CSV" }).click();
    const csv = await readFile((await (await downloaded).path())!, "utf8");
    expect(csv).toContain("East,6");
    expect(csv).toContain("West,12");
    await page.reload();
    await page
      .getByRole("region", { name: "分析历史", exact: true })
      .getByRole("button", {
        name: new RegExp("先检查Schema，并调用get_metric_definition"),
      })
      .click();
    await expect(
      page.getByRole("list", { name: "本次知识来源" }),
    ).toContainText("净贡献额");
    await expect(
      page.getByRole("img", { name: /查询结果柱状图/ }),
    ).toBeVisible();
    const output = path.resolve(process.cwd(), "../data/runtime");
    const prefix = "phase19-exact-name-diagnostic";
    if (!/^[a-z0-9-]{1,40}$/.test(prefix))
      throw new Error("Invalid evidence prefix");
    await mkdir(output, { recursive: true });
    await page.screenshot({
      path: path.join(output, `${prefix}-real-browser.png`),
      fullPage: true,
    });
    await writeFile(
      path.join(output, `${prefix}-real-browser.json`),
      JSON.stringify(
        {
          status: "passed",
          preserves_initial_result: "phase19-live-browser.json 4/5",
          prior_chat_tokens: previous.total_chat_tokens,
          total_chat_tokens:
            previous.total_chat_tokens +
            (analysis.telemetry.usage.total_tokens || 0) +
            (workflow.agent.usage.total_tokens || 0),
          provider: analysis.provider,
          model: analysis.model,
          sql: analysis.sql,
          rows: analysis.result.rows,
          chart: analysis.result.chart,
          telemetry: analysis.telemetry,
          history_and_csv: "passed",
        },
        null,
        2,
      ),
      { flag: "wx" },
    );
  } finally {
    if (datasetId)
      expect(
        (await request.delete(`/api/datasets/${datasetId}`)).status(),
      ).toBe(204);
  }
});
