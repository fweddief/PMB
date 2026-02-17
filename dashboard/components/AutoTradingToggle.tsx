"use client";

import { useState } from "react";

interface Props {
  initialEnabled: boolean;
}

export function AutoTradingToggle({ initialEnabled }: Props) {
  const [enabled, setEnabled] = useState(initialEnabled);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resetting, setResetting] = useState(false);
  const apiBase = process.env.NEXT_PUBLIC_BOT_API_URL;

  const toggle = async () => {
    try {
      setLoading(true);
      setError(null);
      const res = await fetch(`${apiBase}/settings/auto-trading`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ enabled: !enabled }),
      });
      if (!res.ok) {
        throw new Error("Request failed");
      }
      const data = (await res.json()) as { auto_trading_enabled: boolean };
      setEnabled(data.auto_trading_enabled);
    } catch (err) {
      setError("Unable to update auto-trading");
    } finally {
      setLoading(false);
    }
  };

  const resetPaper = async () => {
    try {
      setResetting(true);
      setError(null);
      const res = await fetch(`${apiBase}/paper/reset`, {
        method: "POST",
      });
      if (!res.ok) throw new Error("Reset failed");
    } catch (err) {
      setError("Unable to reset paper account");
    } finally {
      setResetting(false);
    }
  };

  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm uppercase tracking-wide text-slate-500">Auto Trading</p>
          <p className="text-2xl font-semibold text-white">{enabled ? "Enabled" : "Disabled"}</p>
          {error && <p className="text-sm text-rose-400">{error}</p>}
        </div>
        <div className="flex flex-col items-end gap-2 sm:flex-row">
          <button
            onClick={toggle}
            disabled={loading}
            className={`rounded-full px-6 py-2 font-semibold transition ${
              enabled
                ? "bg-rose-500 hover:bg-rose-400 text-white"
                : "bg-emerald-500 hover:bg-emerald-400 text-slate-900"
            } ${loading ? "opacity-60" : ""}`}
          >
            {loading ? "Working..." : enabled ? "Stop Auto Trading" : "Start Auto Trading"}
          </button>
          <button
            onClick={resetPaper}
            disabled={resetting}
            className="rounded-full border border-slate-700 px-4 py-2 text-sm text-slate-300 transition hover:border-slate-500"
          >
            {resetting ? "Resetting..." : "Reset Paper Account"}
          </button>
        </div>
      </div>
    </div>
  );
}
