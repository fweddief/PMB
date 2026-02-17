"use client";

import { Position } from "../lib/types";

interface Props {
  positions: Position[];
}

export function PositionsTable({ positions }: Props) {
  if (!positions.length) {
    return (
      <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6 text-sm text-slate-400">
        No active positions. Deploy capital to open trades.
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold text-white">Active Markets</h3>
        <p className="text-sm text-slate-400">{positions.length} open positions</p>
      </div>
      <div className="mt-4 overflow-x-auto">
        <table className="w-full table-auto text-sm">
          <thead className="text-left text-slate-400">
            <tr>
              <th className="pb-3">Market</th>
              <th className="pb-3">Bracket</th>
              <th className="pb-3 text-right">Shares</th>
              <th className="pb-3 text-right">Avg Cost</th>
              <th className="pb-3 text-right">Price</th>
              <th className="pb-3 text-right">Value</th>
              <th className="pb-3 text-right">Unrealized P&L</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {positions.map((pos) => (
              <tr key={pos.outcome_id}>
                <td className="py-2 text-slate-400">{pos.market_title ?? "N/A"}</td>
                <td className="py-2 text-white">{pos.bracket}</td>
                <td className="py-2 text-right">{pos.shares.toFixed(2)}</td>
                <td className="py-2 text-right">${pos.average_cost.toFixed(4)}</td>
                <td className="py-2 text-right">${(pos.current_price ?? 0).toFixed(4)}</td>
                <td className="py-2 text-right">${(pos.current_value ?? 0).toFixed(2)}</td>
                <td
                  className={`py-2 text-right ${
                    pos.unrealized_pnl >= 0 ? "text-emerald-300" : "text-rose-400"
                  }`}
                >
                  ${pos.unrealized_pnl.toFixed(2)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
