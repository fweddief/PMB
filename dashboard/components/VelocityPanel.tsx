"use client";

import { Prediction } from "../lib/types";

interface Props {
  prediction: Prediction | null;
}

const velocityLabels = {
  velocity_5m: "5m",
  velocity_20m: "20m",
  velocity_1h: "1h",
  velocity_6h: "6h",
  velocity_24h: "24h",
  tweets_24h_total: "24h total",
} as const;

type VelocityKey = keyof typeof velocityLabels;

export function VelocityPanel({ prediction }: Props) {
  if (!prediction?.velocity) return null;
  const metricsOrder = Object.keys(velocityLabels) as VelocityKey[];
  const entries = metricsOrder
    .map((key) => [key, prediction.velocity?.[key]] as const)
    .filter(([, value]) => value !== undefined && value !== null);
  if (!entries.length) return null;

  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
      <h3 className="text-lg font-semibold text-white">Rolling Velocity</h3>
      <p className="text-sm text-slate-400">Tweets per hour with acceleration insights</p>
      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        {entries.map(([key, value]) => {
          const formatted =
            key === "tweets_24h_total"
              ? Number(value).toFixed(0)
              : Number(value).toFixed(2);
          return (
            <div key={key} className="rounded-xl border border-slate-800 bg-slate-950/40 p-4">
              <p className="text-sm text-slate-400">{velocityLabels[key]}</p>
              <p className="text-2xl font-semibold text-white">{formatted}</p>
            </div>
          );
        })}
        {prediction.velocity.acceleration !== undefined && (
          <div className="rounded-xl border border-slate-800 bg-slate-950/40 p-4">
            <p className="text-sm text-slate-400">Acceleration</p>
            <p className="text-2xl font-semibold text-white">
              {prediction.velocity.acceleration > 0 ? "+" : ""}
              {prediction.velocity.acceleration?.toFixed(2)}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
