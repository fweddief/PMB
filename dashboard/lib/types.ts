export interface Prediction {
  market_id?: string;
  market_title?: string;
  week_start?: string;
  week_end?: string;
  current_count: number;
  current_time?: string;
  predicted_total: number;
  confidence_lower: number;
  confidence_upper: number;
  confidence_pct: number;
  market_progress: number;  // % complete (0% until market starts)
  elapsed_days?: number;
  tweets_per_day: number;
  tweets_per_hour: number;
  days_remaining: number;
  mu: number;
  sigma: number;
  velocity?: {
    velocity_5m?: number;
    velocity_20m?: number;
    velocity_1h?: number;
    velocity_6h?: number;
    velocity_24h?: number;
    acceleration?: number;
    tweets_24h_total?: number;
  };
  velocity_snapshot?: {
    velocity_5m?: number | null;
    velocity_20m?: number | null;
    velocity_1h?: number | null;
    velocity_6h?: number | null;
    velocity_24h?: number | null;
    acceleration?: number | null;
  };
  rolling_velocity?: {
    velocity_1h?: number;
    velocity_6h?: number;
    velocity_24h?: number;
    acceleration?: number;
    tweets_24h_total?: number;
  };
  distribution?: {
    intervals?: Record<string, [number, number]>;
  };
}

export interface Recommendation {
  bracket: string;
  market_price: number;
  our_prob: number;
  edge: number;
  action: string;
  timing: string;
  position_size: number;
  shares: number;
  is_sell?: boolean;
  roi_if_win?: number;
  market_title?: string;
  market_id?: string;
}

export interface Trade {
  id: number;
  timestamp: string;
  market_title?: string;
  bracket: string;
  side: string;
  shares: number;
  price: number;
  total_cost: number;
  action?: string;
  edge?: number;
  realized_pnl?: number;
}

export interface DashboardData {
  status: any;
  prediction: Prediction | null;
  recommendations: Recommendation[];
  portfolio: Portfolio | null;
  settings: Settings | null;
  allPredictions?: Prediction[];
  allTrades?: Trade[];
}

export interface Portfolio {
  balance: Balance;
  positions: Position[];
}

export interface Balance {
  cash: number;
  position_value: number;
  total_value: number;
  pnl: number;
  pnl_pct: number;
}

export interface Position {
  outcome_id: string;
  bracket: string;
  shares: number;
  average_cost: number;
  current_price: number;
  current_value: number;
  unrealized_pnl: number;
  market_id?: string;
  market_title?: string;
}

export interface Settings {
  auto_trading_enabled: boolean;
}
