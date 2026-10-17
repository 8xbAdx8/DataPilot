import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "DataPilot · AI 数据分析",
  description: "上传数据，用自然语言探索真实查询结果。",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="zh-CN" className="h-full antialiased">
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
