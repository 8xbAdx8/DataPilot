import { test, expect } from "./fixtures";

test("ambiguous intent asks clarification and a specific follow-up executes real SQL", async ({
  page,
  request,
}) => {
  const upload = await request.post("/api/datasets", {
    multipart: {
      file: {
        name: "clarification.csv",
        mimeType: "text/csv",
        buffer: Buffer.from("value\n10\n20\n"),
      },
    },
  });
  expect(upload.status()).toBe(201);
  const dataset = await upload.json();
  try {
    await page.goto("/");
    await expect(page.getByText("服务已就绪")).toBeVisible();
    await page.getByLabel("分析问题").fill("分析一下");
    await page.getByRole("button", { name: "执行分析" }).click();
    await expect(
      page.getByText("需要补充信息，本次尚未生成或执行 SQL。"),
    ).toBeVisible();
    await expect(
      page.getByText("请明确需要分析的指标、维度和时间范围。", { exact: true }),
    ).toBeVisible();
    await page.getByLabel("分析问题").fill("这个数据集有多少行？");
    await page.getByRole("button", { name: "执行分析" }).click();
    await expect(
      page.getByText("实际查询返回 1 行、1 列。", { exact: true }),
    ).toBeVisible();
    const history = await (
      await request.get(`/api/analyses?dataset_id=${dataset.id}`)
    ).json();
    expect(
      history.some(
        (item: { status: string }) => item.status === "clarification",
      ),
    ).toBe(true);
    expect(
      history.some((item: { status: string }) => item.status === "success"),
    ).toBe(true);
  } finally {
    await request.delete(`/api/datasets/${dataset.id}`);
  }
});
