import { test, expect, createAccount } from "./fixtures";
import type {
  Analysis,
  EvidenceSnapshot,
  KnowledgeCitation,
} from "../src/lib/api";

test("synthetic evidence contracts remain distinct in results, history, dark mode and narrow layouts", async ({
  page,
  request,
}) => {
  const evidenceAccount = createAccount();
  expect(
    (await request.post("/api/auth/login", { data: evidenceAccount })).status(),
  ).toBe(200);
  const upload = await request.post("/api/datasets", {
    multipart: {
      file: {
        name: "evidence-ui.csv",
        mimeType: "text/csv",
        buffer: Buffer.from("value\n10\n20\n"),
      },
    },
  });
  expect(upload.status()).toBe(201);
  const dataset = await upload.json();
  try {
    const doc = await request.post(`/api/datasets/${dataset.id}/knowledge`, {
      data: {
        title: "合成来源定义",
        source_type: "metric_definition",
        content: "合成来源定义：明确统计 SUM(value)。",
      },
    });
    expect(doc.status()).toBe(201);
    const search = await request.post(
      `/api/datasets/${dataset.id}/knowledge/search`,
      { data: { query: "合成来源定义：明确统计 SUM(value)。" } },
    );
    const source = (await search.json()).citations[0] as KnowledgeCitation;
    expect(source.document_id).toBe((await doc.json()).id);
    const generated = await request.post(
      `/api/datasets/${dataset.id}/analyses`,
      { data: { question: "这个数据集有多少行？" } },
    );
    expect(generated.status()).toBe(201);
    const base = (await generated.json()) as Analysis;
    const id = source.chunk_id;
    const initial: EvidenceSnapshot = {
      version: 1,
      policy: "registered",
      status: "resolved",
      source_snapshots: [source],
      source_origins: [{ chunk_id: id, origin: "vector_candidate" }],
      eligible_source_ids: [id],
      selected_source_ids: [id],
      context_source_ids: [id],
      used_source_ids: [id],
      sql_validation: "approved",
      metric_validation: "approved",
      execution: "success",
      final_decision: "answer",
    };
    let current = base;
    await page.route("**/api/**", async (route) => {
      const url = new URL(route.request().url());
      if (
        url.pathname === `/api/datasets/${dataset.id}/analyses` &&
        route.request().method() === "POST"
      ) {
        if (["error", "rejected"].includes(current.status)) {
          await route.fulfill({
            status: 422,
            json: {
              detail: {
                analysis_id: current.id,
                code: current.error_code,
                message: current.error_message,
              },
            },
          });
        } else await route.fulfill({ status: 201, json: current });
      } else if (url.pathname === "/api/analyses")
        await route.fulfill({ json: [current] });
      else if (url.pathname === `/api/analyses/${base.id}`)
        await route.fulfill({ json: current });
      else await route.continue();
    });
    const cases: {
      name: string;
      status?: Analysis["status"];
      candidate?: string;
      evidence?: Partial<EvidenceSnapshot>;
      visible: string[];
      legacy?: boolean;
      error?: string;
    }[] = [
      {
        name: "向量命中",
        candidate: "retrieved",
        visible: ["找到候选", "执行成功后确认使用"],
      },
      {
        name: "目录补充",
        candidate: "no_match",
        evidence: {
          source_origins: [{ chunk_id: id, origin: "registered_catalog" }],
        },
        visible: [
          "未命中",
          "登记目录补充 1 条",
          "本次包含登记目录补充，不代表初始向量检索命中。",
        ],
      },
      {
        name: "无匹配",
        status: "clarification",
        candidate: "no_match",
        evidence: {
          status: "not_found",
          source_snapshots: [],
          source_origins: [],
          eligible_source_ids: [],
          selected_source_ids: [],
          context_source_ids: [],
          used_source_ids: [],
          sql_validation: "not_requested",
          metric_validation: "not_requested",
          execution: "not_requested",
          final_decision: "clarify",
        },
        visible: ["未找到登记指标", "没有保存可展示的授权来源片段。"],
      },
      {
        name: "定义歧义",
        status: "clarification",
        candidate: "retrieved",
        evidence: {
          status: "ambiguous",
          eligible_source_ids: [],
          selected_source_ids: [],
          context_source_ids: [],
          used_source_ids: [],
          sql_validation: "not_requested",
          metric_validation: "not_requested",
          execution: "not_requested",
          final_decision: "clarify",
        },
        visible: ["多个定义冲突，需要澄清", "未确认指标采用"],
      },
      {
        name: "定义不适用",
        status: "clarification",
        candidate: "retrieved",
        evidence: {
          status: "inapplicable",
          eligible_source_ids: [],
          selected_source_ids: [],
          context_source_ids: [],
          used_source_ids: [],
          sql_validation: "not_requested",
          metric_validation: "not_requested",
          execution: "not_requested",
          final_decision: "clarify",
        },
        visible: ["定义不适用于当前字段", "DuckDB：未执行"],
      },
      {
        name: "SQL拒绝",
        status: "rejected",
        candidate: "retrieved",
        evidence: {
          used_source_ids: [],
          sql_validation: "rejected",
          metric_validation: "not_requested",
          execution: "not_requested",
          final_decision: "reject",
        },
        error: "合成SQL校验失败",
        visible: [
          "计划已选择",
          "已进入 SQL 上下文",
          "未确认指标采用",
          "SQL 安全：拒绝",
        ],
      },
      {
        name: "执行超时",
        status: "error",
        candidate: "retrieved",
        evidence: {
          used_source_ids: [],
          execution: "error",
          final_decision: "error",
        },
        error: "合成查询超时",
        visible: ["已进入 SQL 上下文", "未确认指标采用", "DuckDB：失败"],
      },
      {
        name: "选择未传递",
        candidate: "retrieved",
        evidence: { context_source_ids: [], used_source_ids: [] },
        visible: ["计划已选择", "未进入 SQL 上下文", "未确认指标采用"],
      },
      {
        name: "成功但无指标证明",
        candidate: "retrieved",
        evidence: { metric_validation: "not_requested", used_source_ids: [] },
        visible: ["已进入 SQL 上下文", "未确认指标采用", "指标契约：未进行"],
      },
      {
        name: "旧历史",
        legacy: true,
        candidate: "no_match",
        visible: ["历史记录未包含该证据", "不推断初始向量结果"],
      },
      {
        name: "来源为空",
        candidate: "no_documents",
        evidence: {
          source_snapshots: [],
          source_origins: [],
          selected_source_ids: [],
          context_source_ids: [],
          used_source_ids: [],
          eligible_source_ids: [],
          metric_validation: "not_requested",
        },
        visible: ["没有就绪知识", "没有保存可展示的授权来源片段。"],
      },
      {
        name: "部分元数据",
        candidate: "retrieved",
        evidence: {
          selected_source_ids: undefined,
          context_source_ids: undefined,
          used_source_ids: undefined,
          sql_validation: undefined,
          metric_validation: undefined,
          execution: undefined,
          final_decision: undefined,
        },
        visible: ["计划选择未记录", "上下文传递未记录", "确认使用未记录"],
      },
    ];
    cases.push({
      name: "不完整来源详情",
      candidate: "retrieved",
      evidence: {
        source_snapshots: [
          {
            ...source,
            title: undefined,
            excerpt: undefined,
            chunk_index: undefined,
          } as unknown as KnowledgeCitation,
        ],
        selected_source_ids: undefined,
        context_source_ids: undefined,
        used_source_ids: undefined,
      },
      visible: ["来源标题未记录", "片段 未记录", "确认使用未记录"],
    });
    for (const scenario of cases) {
      const evidence = { ...initial, ...scenario.evidence };
      current = {
        ...base,
        question: `合成证据：${scenario.name}`,
        status: scenario.status ?? "success",
        error_message: scenario.error ?? null,
        error_code: scenario.error ? "query_failed" : null,
        result: scenario.status ? null : base.result,
        summary:
          scenario.status === "clarification"
            ? { clarification_questions: ["请补充合成口径"] }
            : base.summary,
        telemetry: {
          ...base.telemetry,
          workflow: {
            ...base.telemetry!.workflow!,
            rag: {
              status: scenario.legacy ? "no_match" : "retrieved",
              used: true,
              source: "fake",
              citations: [source],
              candidate_status: scenario.candidate,
              candidate_stages: [
                {
                  stage: "vector",
                  candidates:
                    scenario.candidate === "retrieved"
                      ? [{ chunk_id: id, similarity: 0.8 }]
                      : [],
                },
              ],
              ...(scenario.legacy ? {} : { evidence }),
            },
          },
        },
      };
      await page.goto("/");
      await page.getByLabel("分析问题").fill(current.question);
      await page.getByRole("button", { name: "执行分析" }).click();
      const chain = page.getByLabel("来源证据链", { exact: true });
      for (const text of scenario.visible)
        await expect(chain).toContainText(text);
      await expect(chain).toBeVisible();
      await expect(
        chain.getByText(scenario.visible[0], { exact: false }).first(),
      ).toBeVisible();
      if (scenario.name !== "向量命中" && scenario.name !== "目录补充") {
        await expect(
          chain.getByText("执行成功后确认使用", { exact: true }),
        ).toHaveCount(0);
      }
      await expect(
        page
          .getByRole("region", { name: "分析历史" })
          .getByRole("button", { name: new RegExp(current.question) }),
      ).toBeVisible();
      await page.reload();
      await page
        .getByRole("region", { name: "分析历史" })
        .getByRole("button", { name: new RegExp(current.question) })
        .click();
      for (const text of scenario.visible)
        await expect(chain).toContainText(text);
      await expect(chain).toBeVisible();
      await expect(
        chain.getByText(scenario.visible[0], { exact: false }).first(),
      ).toBeVisible();
    }
    await page.emulateMedia({ colorScheme: "dark" });
    await page.setViewportSize({ width: 390, height: 844 });
    const panel = page.getByRole("region", { name: "分析工作流" });
    await expect(panel).toHaveCSS("background-color", "rgb(15, 23, 42)");
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(390);
    await panel.screenshot({ path: "test-results/evidence-dark-mobile.png" });
  } finally {
    await request.delete(`/api/datasets/${dataset.id}`);
  }
});

test("real saved evidence survives refresh, knowledge deletion and re-login without mocked history", async ({
  page,
  request,
}) => {
  const account = createAccount();
  expect(
    (await request.post("/api/auth/login", { data: account })).status(),
  ).toBe(200);
  const uploaded = await request.post("/api/datasets", {
    multipart: {
      file: {
        name: "evidence-persistence.csv",
        mimeType: "text/csv",
        buffer: Buffer.from("value\n10\n20\n"),
      },
    },
  });
  const dataset = await uploaded.json();
  try {
    const doc = await request.post(`/api/datasets/${dataset.id}/knowledge`, {
      data: {
        title: "学分",
        source_type: "field_alias",
        content: '{"alias":"学分","field":"value"}',
      },
    });
    expect(doc.status()).toBe(201);
    await page.goto("/");
    await page.getByLabel("分析问题").fill("学分总和");
    const completed = page.waitForResponse(
      (r) => r.url().endsWith("/analyses") && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "执行分析" }).click();
    const saved = (await (await completed).json()) as Analysis;
    expect(saved.status).toBe("success");
    expect(saved.result?.rows).toEqual([{ metric: 30 }]);
    const evidence = saved.telemetry!.workflow!.rag!.evidence!;
    expect(evidence.selected_source_ids).toHaveLength(1);
    expect(evidence.context_source_ids).toHaveLength(1);
    expect(evidence.used_source_ids).toEqual([]);
    const chain = page.getByLabel("来源证据链", { exact: true });
    await expect(chain).toContainText("学分");
    await expect(chain).toContainText("未确认指标采用");
    expect(
      (
        await request.delete(`/api/knowledge/${(await doc.json()).id}`)
      ).status(),
    ).toBe(204);
    expect((await request.post("/api/auth/logout")).status()).toBe(204);
    expect(
      (await request.post("/api/auth/login", { data: account })).status(),
    ).toBe(200);
    await page.reload();
    await page
      .getByRole("region", { name: "分析历史" })
      .getByRole("button", { name: /学分总和/ })
      .click();
    await expect(chain).toContainText("学分");
    await expect(chain).toContainText("已进入 SQL 上下文");
    await expect(chain).toContainText("未确认指标采用");
    const restored = await request.get(`/api/analyses/${saved.id}`);
    expect((await restored.json()).telemetry.workflow.rag.evidence).toEqual(
      evidence,
    );
  } finally {
    await request.delete(`/api/datasets/${dataset.id}`);
  }
});
