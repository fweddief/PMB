"use client";

import clsx from "clsx";
import { Recommendation } from "../lib/types";

interface Props {
  recommendations: Recommendation[];
}

const actionColors: Record<string, string> = {
  BUY: "text-emerald-400",
  "STRONG BUY": "text-emerald-300 font-semibold",
  "SMALL BUY": "text-emerald-200",
  "TAKE PROFIT": "text-amber-300",
  "STOP LOSS": "text-rose-400",
  SELL: "text-rose-400",
};

export function RecommendationsTable({ recommendations }: Props) {
  if (!recommendations.length) {
    return (
      <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6 text-sm text-slate-400">
        No trades meet the configured thresholds yet.
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold text-white">Trading Recommendations</h3>
          <p className="text-sm text-slate-400">Kelly-sized positions with edge &gt; thresholds</p>
        </div>
      </div>
      <div className="mt-4 overflow-x-auto">
        <table className="w-full table-auto text-sm">
          <thead className="text-left text-slate-400">
            <tr>
              <th className="pb-3">Market</th>
              <th className="pb-3">Bracket</th>
              <th className="pb-3">Action</th>
              <th className="pb-3">Timing</th>
              <th className="pb-3 text-right">Edge %</th>
              <th className="pb-3 text-right">Prob %</th>
              <th className="pb-3 text-right">Market</th>
              <th className="pb-3 text-right">Size ($)</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {recommendations.slice(0, 12).map((rec) => (
              <tr key={`${rec.bracket}-${rec.action}`} className="text-slate-200">
                <td className="py-2 text-slate-400">{rec.market_title ?? "N/A"}</td>
                <td className="py-2 font-medium">{rec.bracket}</td>
                <td className={clsx("py-2", actionColors[rec.action] || "text-blue-200")}>{rec.action}</td>
                <td className="py-2 text-slate-400">{rec.timing}</td>
                <td className="py-2 text-right">{rec.edge.toFixed(1)}%</td>
                <td className="py-2 text-right">{rec.our_prob.toFixed(1)}%</td>
                <td className="py-2 text-right">${rec.market_price.toFixed(3)}</td>
                <td className="py-2 text-right">${rec.position_size.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
