"use client";

import { Portfolio } from "../lib/types";
import { StatCard } from "./StatCard";

interface Props {
  portfolio: Portfolio | null;
}

export function PortfolioOverview({ portfolio }: Props) {
  if (!portfolio) {
    return (
      <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6 text-sm text-slate-400">
        No portfolio data available.
      </div>
    );
  }

  const { balance } = portfolio;

  return (
    <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
      <StatCard label="Cash" value={`$${balance.cash.toFixed(2)}`} accent="blue" />
      <StatCard label="Open Positions" value={`$${balance.position_value.toFixed(2)}`} accent="yellow" />
      <StatCard label="Total Equity" value={`$${balance.total_value.toFixed(2)}`} accent="green" />
      <StatCard
        label="P&L"
        value={`$${balance.pnl.toFixed(2)}`}
        sublabel={`${balance.pnl_pct.toFixed(1)}%`}
        accent={balance.pnl >= 0 ? "green" : "red"}
      />
    </div>
  );
}
