"use client";

import { Prediction } from "../lib/types";
import { StatCard } from "./StatCard";

interface Props {
  prediction: Prediction | null;
}

export function PredictionCard({ prediction }: Props) {
  if (!prediction) {
    return (
      <div className="rounded-2xl border border-dashed border-slate-700 bg-slate-900/40 p-6 text-sm text-slate-400">
        Waiting for prediction data...
      </div>
    );
  }

  const { market_title, current_count, predicted_total, confidence_lower, confidence_upper, market_progress, tweets_per_day, tweets_per_hour, velocity } =
    prediction;

  return (
    <div className="space-y-4">
      {market_title && (
        <div className="rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-2">
          <h3 className="text-sm font-medium text-slate-400">Current Market</h3>
          <p className="text-base font-semibold text-white">{market_title}</p>
        </div>
      )}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Current Tweets" value={current_count} sublabel="This market week" accent="green" />
        <StatCard
          label="Predicted Total"
          value={predicted_total}
          sublabel={`Range ${confidence_lower} - ${confidence_upper}`}
          accent="yellow"
        />
        <StatCard
          label="Market Progress"
          value={`${market_progress?.toFixed(1) ?? '0.0'}%`}
          sublabel={`${tweets_per_day.toFixed(1)} tweets/day`}
          accent="blue"
        />
        <StatCard
          label="Velocity"
          value={`${tweets_per_hour.toFixed(2)} / hr`}
          sublabel={`1h: ${(velocity?.velocity_1h ?? 0).toFixed(2)} | 6h: ${(velocity?.velocity_6h ?? 0).toFixed(2)}`}
          accent="green"
        />
      </div>
    </div>
  );
}
