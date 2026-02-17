import { Trade } from "../lib/types";

interface TradesTableProps {
  trades: Trade[];
}

export function TradesTable({ trades }: TradesTableProps) {
  if (!trades || trades.length === 0) {
    return (
      <section className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6 backdrop-blur">
        <h2 className="mb-4 text-lg font-medium text-white">Past Trades</h2>
        <p className="text-sm text-slate-400">No trades yet</p>
      </section>
    );
  }

  return (
    <section className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6 backdrop-blur">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-lg font-medium text-white">Past Trades</h2>
        <span className="text-sm text-slate-400">{trades.length} total trades</span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-slate-800">
              <th className="pb-3 font-medium text-slate-400">Time</th>
              <th className="pb-3 font-medium text-slate-400">Market</th>
              <th className="pb-3 font-medium text-slate-400">Bracket</th>
              <th className="pb-3 font-medium text-slate-400">Side</th>
              <th className="pb-3 text-right font-medium text-slate-400">Shares</th>
              <th className="pb-3 text-right font-medium text-slate-400">Price</th>
              <th className="pb-3 text-right font-medium text-slate-400">Total</th>
              <th className="pb-3 text-right font-medium text-slate-400">Edge</th>
              <th className="pb-3 text-right font-medium text-slate-400">P&L</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((trade) => {
              const isBuy = trade.side === "BUY";
              const hasProfit = trade.realized_pnl && trade.realized_pnl > 0;

              return (
                <tr
                  key={trade.id}
                  className="border-b border-slate-800/50 last:border-0"
                >
                  <td className="py-3 text-slate-300">
                    {new Date(trade.timestamp).toLocaleString('en-US', {
                      month: 'short',
                      day: 'numeric',
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                  </td>
                  <td className="py-3 text-slate-300">
                    <div className="max-w-[200px] truncate">
                      {trade.market_title || 'N/A'}
                    </div>
                  </td>
                  <td className="py-3 text-slate-300">{trade.bracket}</td>
                  <td className="py-3">
                    <span
                      className={`inline-block rounded px-2 py-1 text-xs font-medium ${
                        isBuy
                          ? 'bg-emerald-500/20 text-emerald-400'
                          : 'bg-rose-500/20 text-rose-400'
                      }`}
                    >
                      {trade.side}
                    </span>
                  </td>
                  <td className="py-3 text-right text-slate-300">
                    {trade.shares.toFixed(2)}
                  </td>
                  <td className="py-3 text-right text-slate-300">
                    ${trade.price.toFixed(4)}
                  </td>
                  <td className="py-3 text-right text-slate-300">
                    ${Math.abs(trade.total_cost).toFixed(2)}
                  </td>
                  <td className="py-3 text-right text-slate-300">
                    {trade.edge ? `${trade.edge.toFixed(1)}%` : '-'}
                  </td>
                  <td className="py-3 text-right">
                    {trade.realized_pnl ? (
                      <span
                        className={
                          hasProfit ? 'text-emerald-400' : 'text-rose-400'
                        }
                      >
                        ${trade.realized_pnl.toFixed(2)}
                      </span>
                    ) : (
                      <span className="text-slate-500">-</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
