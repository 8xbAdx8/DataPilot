"use client";

import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { BarChart, LineChart, PieChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  AriaComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { EChartsOption } from "echarts";
import type { QueryResult } from "@/lib/api";

echarts.use([
  BarChart,
  LineChart,
  PieChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  AriaComponent,
  CanvasRenderer,
]);

export function ResultChart({ result }: { result: QueryResult }) {
  const ref = useRef<HTMLDivElement>(null);
  const { kind, x, y } = result.chart;
  useEffect(() => {
    if (!ref.current || kind === "table" || !x || !y) return;
    const chart = echarts.init(ref.current);
    const labels = result.rows.map((row) => String(row[x]));
    const values = result.rows.map((row) => row[y] as number);
    const option: EChartsOption = {
      animation: false,
      color: ["#079878", "#4e83cf", "#e7ab47", "#9c83d9"],
      aria: {
        enabled: true,
        label: {
          description: `查询结果${kind === "bar" ? "柱状图" : kind === "line" ? "折线图" : "饼图"}：${labels.map((name, i) => `${name}：${values[i]}`).join("；")}`,
        },
      },
      tooltip: {
        trigger: kind === "pie" ? "item" : "axis",
        renderMode: "richText",
      },
      ...(kind === "pie"
        ? {
            legend: { bottom: 0 },
            series: [
              {
                type: "pie",
                radius: ["40%", "65%"],
                data: labels.map((name, i) => ({ name, value: values[i] })),
              },
            ],
          }
        : {
            grid: {
              left: 60,
              right: 24,
              bottom: 65,
              top: 25,
              containLabel: true,
            },
            xAxis: {
              type: "category",
              data: labels,
              axisLabel: {
                hideOverlap: true,
                rotate: labels.length > 8 ? 25 : 0,
              },
            },
            yAxis: {
              type: "value",
              name: y,
              splitLine: { lineStyle: { color: "#eef2f6" } },
            },
            series: [{ name: y, type: kind, data: values, barMaxWidth: 55 }],
          }),
    };
    chart.setOption(option);
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(ref.current);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [result, kind, x, y]);
  if (kind === "table")
    return (
      <p className="rounded-md bg-slate-50 p-3 text-xs text-slate-500">
        {result.chart.reason} 已保留完整返回表格。
      </p>
    );
  return (
    <div
      ref={ref}
      role="img"
      aria-label={`查询结果${kind === "bar" ? "柱状图" : kind === "line" ? "折线图" : "饼图"}`}
      className="h-80 w-full"
    />
  );
}
