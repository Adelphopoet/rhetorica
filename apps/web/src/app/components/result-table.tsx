"use client";

import { motion } from "framer-motion";
import { Badge } from "@rhetorica/ui";
import { Table as TableIcon } from "lucide-react";

interface ResultTableProps {
  columns: string[];
  rows: Array<Array<string | number | boolean | null>>;
  emptyMessage?: string;
}

export function ResultTable({
  columns,
  rows,
  emptyMessage = "Выполните запрос, чтобы увидеть результаты",
}: ResultTableProps) {
  if (!rows || rows.length === 0) {
    return (
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        className="flex flex-col items-center justify-center py-12 text-center"
      >
        <TableIcon className="h-12 w-12 text-muted mb-4" />
        <p className="text-sm text-secondary">{emptyMessage}</p>
      </motion.div>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="overflow-auto rounded-lg border border-border"
    >
      <table className="w-full border-collapse min-w-[560px]">
        <thead>
          <tr>
            {columns.map((column) => (
              <th
                key={column}
                className="sticky top-0 bg-elevated text-secondary font-semibold text-xs uppercase tracking-wider px-3 py-2 text-left border-b border-border"
              >
                <div className="flex items-center gap-2">
                  {column}
                  <Badge variant="info">{column.length}</Badge>
                </div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <motion.tr
              key={`row-${rowIndex}`}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ duration: 0.2, delay: rowIndex * 0.03 }}
              className="border-b border-border hover:bg-elevated/50 transition-colors"
            >
              {row.map((value, colIndex) => (
                <td
                  key={`cell-${rowIndex}-${colIndex}`}
                  className="px-3 py-2 text-sm text-primary font-mono"
                >
                  {value === null ? (
                    <span className="text-muted italic">NULL</span>
                  ) : typeof value === "boolean" ? (
                    <Badge variant={value ? "success" : "danger"}>
                      {value.toString()}
                    </Badge>
                  ) : typeof value === "number" ? (
                    <span className="tabular-nums">{value}</span>
                  ) : (
                    String(value)
                  )}
                </td>
              ))}
            </motion.tr>
          ))}
        </tbody>
      </table>
    </motion.div>
  );
}
