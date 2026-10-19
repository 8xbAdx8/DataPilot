import { test, expect } from "@playwright/test";

test.afterEach(async ({ request }) => {
  const datasets = await (await request.get("/api/datasets")).json();
  for (const dataset of datasets.filter((d: { name: string }) =>
    d.name.startsWith("e2e-history-"),
  )) {
    expect((await request.delete(`/api/datasets/${dataset.id}`)).status()).toBe(
      204,
    );
  }
});

test("history searches real persisted SQL, combines status filters and paginates", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const uploaded = await request.post("/api/datasets", {
    multipart: {
      file: {
        name: "e2e-history-pages.csv",
        mimeType: "text/csv",
        buffer: Buffer.from("region,revenue\nEast,10\nWest,20\n"),
      },
    },
  });
  expect(uploaded.status()).toBe(201);
  const dataset = await uploaded.json();
  for (let i = 0; i < 21; i++) {
    const response = await request.post(
      `/api/datasets/${dataset.id}/analyses`,
      {
        data: { question: i === 0 ? "这个数据集有多少行？" : "预览数据" },
      },
    );
    expect(response.status()).toBe(201);
  }
  const failed = await request.post(`/api/datasets/${dataset.id}/analyses`, {
    data: { question: "无法识别的问题" },
  });
  expect(failed.status()).toBe(422);
  await page.goto("/");
  const history = page.getByRole("region", { name: "分析历史", exact: true });
  await expect(history.getByRole("button", { name: "下一页" })).toBeEnabled();
  await expect(history.getByRole("button", { name: /预览数据/ })).toHaveCount(
    19,
  );
  await history.getByRole("button", { name: "下一页" }).click();
  await expect(history.getByText("第 2 页")).toBeVisible();
  await expect(
    history.getByRole("button", { name: /这个数据集有多少行/ }),
  ).toBeVisible();
  await expect(history.getByRole("button", { name: "下一页" })).toBeDisabled();
  await history.getByLabel("历史关键词").fill("count(*)");
  await history.getByRole("button", { name: "搜索历史" }).click();
  await expect(history.getByText("第 1 页")).toBeVisible();
  await expect(
    history.getByRole("button", { name: /这个数据集有多少行/ }),
  ).toBeVisible();
  await expect(history.getByRole("button", { name: /预览数据/ })).toHaveCount(
    0,
  );
  await history.getByRole("button", { name: /这个数据集有多少行/ }).click();
  await expect(page.getByLabel("SQL")).toContainText("COUNT(*)");
  await expect(page.getByText("实际返回 1 行", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "执行分析" })).toBeEnabled();
  await history.getByLabel("执行状态").selectOption("error");
  await history.getByRole("button", { name: "搜索历史" }).click();
  await expect(history.getByText("没有符合条件的分析历史。")).toBeVisible();
  await history.getByLabel("历史关键词").fill("");
  await history.getByRole("button", { name: "搜索历史" }).click();
  await expect(
    history.getByRole("button", { name: /无法识别的问题/ }),
  ).toBeVisible();
  await expect(history.getByRole("button", { name: /预览数据/ })).toHaveCount(
    0,
  );
  await history.getByRole("button", { name: "重置筛选" }).click();
  await expect(history.getByRole("button", { name: "下一页" })).toBeEnabled();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  expect(
    (await history.getByLabel("历史关键词").boundingBox())!.width,
  ).toBeGreaterThanOrEqual(200);
  await history.screenshot({ path: "test-results/history-mobile.png" });
});

test("history load errors can be retried without hiding the analysis form", async ({
  page,
  request,
}) => {
  expect(
    (
      await request.post("/api/datasets", {
        multipart: {
          file: {
            name: "e2e-history-retry.csv",
            mimeType: "text/csv",
            buffer: Buffer.from("a,b\n1,2\n"),
          },
        },
      })
    ).status(),
  ).toBe(201);
  let fail = true;
  await page.route("**/api/analyses?*", async (route) => {
    if (fail) {
      await route.fulfill({
        status: 503,
        json: { detail: { message: "历史服务暂不可用" } },
      });
    } else {
      await route.continue();
    }
  });
  await page.goto("/");
  const history = page.getByRole("region", { name: "分析历史", exact: true });
  await expect(history.getByRole("alert")).toContainText("历史服务暂不可用");
  await expect(page.getByLabel("分析问题")).toBeEnabled();
  fail = false;
  await history.getByRole("button", { name: "重试历史加载" }).click();
  await expect(history.getByRole("alert")).toHaveCount(0);
  await expect(history.getByText("没有符合条件的分析历史。")).toBeVisible();
  await page.getByLabel("分析问题").fill("行数");
  await page.getByRole("button", { name: "执行分析" }).click();
  await expect(history.getByRole("button", { name: /行数/ })).toBeVisible();
});
