import { getDashboardData } from "../lib/api";
import { BellCurveChart } from "../components/BellCurveChart";
import { PredictionCard } from "../components/PredictionCard";
import { VelocityPanel } from "../components/VelocityPanel";
import { RecommendationsTable } from "../components/RecommendationsTable";
import { StatusStrip } from "../components/StatusStrip";
import { AutoTradingToggle } from "../components/AutoTradingToggle";
import { PortfolioOverview } from "../components/PortfolioOverview";
import { PositionsTable } from "../components/PositionsTable";
import { TradesTable } from "../components/TradesTable";

// Force dynamic rendering - don't try to statically generate at build time
export const dynamic = 'force-dynamic';
export const revalidate = 0;

export default async function DashboardPage() {
  const dashboard = await getDashboardData();
  const { prediction, recommendations, status, portfolio, settings, allPredictions, allTrades } = dashboard;

  // Use allPredictions if available AND has data, otherwise fallback to single prediction
  // This handles the case where allPredictions returns empty array
  const predictions = allPredictions && allPredictions.length > 0
    ? allPredictions
    : (prediction ? [prediction] : []);

  return (
    <main className="mx-auto max-w-6xl space-y-6 px-4 py-10 md:px-6">
      <header className="space-y-2">
        <p className="text-sm uppercase tracking-wide text-slate-500">Polymarket Tweet Bot</p>
        <h1 className="text-3xl font-semibold text-white">Live Trading Dashboard</h1>
        <p className="text-slate-400">
          Streaming velocity metrics, probabilistic forecasts, and Kelly-sized trades from the backend service.
        </p>
      </header>

      <StatusStrip status={status ?? null} />

      <AutoTradingToggle initialEnabled={settings?.auto_trading_enabled ?? false} />

      <PortfolioOverview portfolio={portfolio ?? null} />

      {/* Show all markets */}
      {predictions.length > 0 ? (
        <div className="space-y-8">
          {predictions.map((pred, index) => (
            <div key={pred.market_id || index} className="space-y-6">
              <PredictionCard prediction={pred} />
              <div className="grid gap-6 lg:grid-cols-3">
                <div className="lg:col-span-2">
                  <BellCurveChart prediction={pred} />
                </div>
                <div className="lg:col-span-1">
                  <VelocityPanel prediction={pred} />
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="rounded-2xl border border-dashed border-slate-700 bg-slate-900/40 p-6 text-sm text-slate-400">
          Waiting for prediction data...
        </div>
      )}

      <PositionsTable positions={portfolio?.positions ?? []} />

      <RecommendationsTable recommendations={recommendations} />

      <TradesTable trades={allTrades ?? []} />
    </main>
  );
}
