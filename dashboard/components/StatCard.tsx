"use client";

import clsx from "clsx";

interface StatCardProps {
  label: string;
  value: string | number;
  sublabel?: string;
  accent?: "green" | "yellow" | "red" | "blue";
}

const accentMap: Record<string, string> = {
  green: "from-emerald-500/20 to-emerald-500/5 border-emerald-500/40",
  yellow: "from-amber-400/20 to-amber-500/5 border-amber-500/40",
  red: "from-rose-500/20 to-rose-500/5 border-rose-500/40",
  blue: "from-sky-500/20 to-sky-500/5 border-sky-500/40",
};

export function StatCard({ label, value, sublabel, accent = "blue" }: StatCardProps) {
  return (
    <div
      className={clsx(
        "rounded-xl border bg-gradient-to-br p-4 transition hover:border-white/40",
        accentMap[accent]
      )}
    >
      <p className="text-sm uppercase tracking-wide text-slate-400">{label}</p>
      <p className="mt-2 text-3xl font-semibold text-white">{value}</p>
      {sublabel && <p className="text-sm text-slate-400">{sublabel}</p>}
    </div>
  );
}
