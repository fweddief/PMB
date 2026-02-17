"use client";

interface Props {
  status: Record<string, any> | null;
}

const statusLabel = (status?: boolean) => (status ? "ONLINE" : "ATTENTION");

export function StatusStrip({ status }: Props) {
  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-4 text-sm text-slate-200">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-wide text-slate-500">Bot Health</p>
          <p className="text-lg font-semibold">
            {status?.working ? "All subsystems running" : "Waiting for data"}
          </p>
        </div>
        <div className="flex gap-4 text-xs">
          <StatusPill label="Tweets" ok={Boolean(status?.tweet_data)} />
          <StatusPill label="Markets" ok={Boolean(status?.market_data)} />
          <StatusPill label="Prices" ok={Boolean(status?.price_data)} />
        </div>
      </div>
      {status?.warnings?.length ? (
        <ul className="mt-2 list-disc pl-5 text-rose-300">
          {status.warnings.map((warning: string) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function StatusPill({ label, ok }: { label: string; ok: boolean }) {
  return (
    <span
      className={`rounded-full px-3 py-1 font-semibold ${
        ok ? "bg-emerald-500/10 text-emerald-300" : "bg-amber-500/10 text-amber-300"
      }`}
    >
      {label}: {statusLabel(ok)}
    </span>
  );
}
