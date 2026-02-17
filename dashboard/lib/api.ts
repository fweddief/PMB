import { DashboardData, Prediction, Recommendation, Portfolio, Settings } from "./types";

const BASE_URL = process.env.NEXT_PUBLIC_BOT_API_URL || "http://localhost:8000";

async function fetchJSON<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${BASE_URL}${path}`, {
      next: { revalidate: 5 },
    });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch (err) {
    console.error(`Failed to fetch ${path}`, err);
    return null;
  }
}

export async function fetchStatus() {
  return fetchJSON<Record<string, unknown>>("/status");
}

export async function fetchPrediction(): Promise<Prediction | null> {
  return fetchJSON<Prediction>("/prediction");
}

export async function fetchAllPredictions(): Promise<Prediction[]> {
  const data = await fetchJSON<{ predictions: Prediction[] }>("/predictions/all");
  return data?.predictions ?? [];
}

export async function fetchRecommendations(): Promise<Recommendation[]> {
  const data = await fetchJSON<{ recommendations: Recommendation[] }>("/recommendations");
  return data?.recommendations ?? [];
}

export async function fetchPortfolio(): Promise<Portfolio | null> {
  return fetchJSON<Portfolio>("/portfolio");
}

export async function fetchSettings(): Promise<Settings | null> {
  return fetchJSON<Settings>("/settings");
}

export async function fetchAllTrades() {
  const data = await fetchJSON<{ trades: any[] }>("/trades/all");
  return data?.trades ?? [];
}

export async function getDashboardData(): Promise<DashboardData> {
  const [status, prediction, recommendations, portfolio, settings, allPredictions, allTrades] = await Promise.all([
    fetchStatus(),
    fetchPrediction(),
    fetchRecommendations(),
    fetchPortfolio(),
    fetchSettings(),
    fetchAllPredictions(),
    fetchAllTrades(),
  ]);

  return {
    status,
    prediction,
    recommendations,
    portfolio,
    settings,
    allPredictions,
    allTrades,
  };
}
