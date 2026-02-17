"use client";

import { useMemo } from "react";
import {
  AreaChart,
  Area,
  ResponsiveContainer,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  YAxis,
  XAxis,
} from "recharts";
import { Prediction } from "../lib/types";

interface Props {
  prediction: Prediction | null;
}

function buildCurve(prediction: Prediction | null) {
  if (!prediction) return [];
  const { mu, sigma } = prediction;
  const points = [];
  const step = sigma / 8 || 1;
  for (let x = mu - sigma * 4; x <= mu + sigma * 4; x += step) {
    const exponent = Math.exp(-0.5 * Math.pow((x - mu) / sigma, 2));
    const y = exponent / (sigma * Math.sqrt(2 * Math.PI));
    points.push({ x: Math.round(x), y });
  }
  return points;
}

export function BellCurveChart({ prediction }: Props) {
  const data = useMemo(() => buildCurve(prediction), [prediction]);
  if (!prediction) return null;

  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold text-white">Model Distribution</h3>
          <p className="text-sm text-slate-400">Normal distribution using μ and σ from velocity model</p>
        </div>
        <div className="text-right">
          <p className="text-sm text-slate-400">μ {prediction.mu.toFixed(1)}</p>
          <p className="text-sm text-slate-400">σ {prediction.sigma.toFixed(1)}</p>
        </div>
      </div>
      <div className="mt-4 h-64 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data}>
            <defs>
              <linearGradient id="colorCurve" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#6366f1" stopOpacity={0.8} />
                <stop offset="95%" stopColor="#6366f1" stopOpacity={0.1} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
            <XAxis dataKey="x" stroke="#94a3b8" />
            <YAxis hide />
            <Tooltip
              contentStyle={{ backgroundColor: "#0f172a", border: "1px solid #1e293b" }}
              labelStyle={{ color: "#e2e8f0" }}
            />
            <Area type="monotone" dataKey="y" stroke="#818cf8" fillOpacity={1} fill="url(#colorCurve)" />
            <ReferenceLine x={prediction.predicted_total} stroke="#fbbf24" label="Prediction" strokeDasharray="3 3" />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
