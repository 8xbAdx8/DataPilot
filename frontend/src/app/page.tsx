export default function Home() {
  return (
    <main className="mx-auto max-w-5xl p-12">
      <p className="text-sm font-semibold text-emerald-700">DATAPILOT / 数据工作台</p>
      <h1 className="mt-4 text-4xl font-semibold">从数据到答案，每一步都有依据。</h1>
      <p className="mt-6 text-slate-600">上传预览与安全分析功能正在分阶段接入。</p>
      <a className="mt-8 inline-block underline" href="http://localhost:8000/docs">打开 FastAPI 接口文档</a>
    </main>
  );
}
