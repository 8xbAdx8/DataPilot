import { Cell, display } from "@/lib/api";

export function DataTable({
  columns,
  rows,
}: {
  columns: string[];
  rows: Record<string, Cell>[];
}) {
  if (!rows.length)
    return (
      <p className="p-6 text-sm text-slate-500">
        查询成功，但没有符合条件的数据。
      </p>
    );
  return (
    <div className="max-h-96 overflow-auto rounded-lg border border-slate-200">
      <table className="w-full border-collapse text-left text-sm">
        <thead className="sticky top-0 bg-slate-100 text-slate-600">
          <tr>
            <th className="w-12 px-4 py-3 font-medium">#</th>
            {columns.map((c) => (
              <th key={c} className="whitespace-nowrap px-4 py-3 font-medium">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-t border-slate-100 hover:bg-slate-50">
              <td className="px-4 py-3 text-slate-400">{i + 1}</td>
              {columns.map((c) => (
                <td
                  key={c}
                  title={display(row[c])}
                  className="max-w-80 truncate px-4 py-3 tabular-nums"
                >
                  {display(row[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
