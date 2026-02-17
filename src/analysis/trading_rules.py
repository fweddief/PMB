"""
Enhanced trading rules based on statistical edge and market conditions.

Key principles:
1. Early week: Small lottery tickets on extreme outcomes (0.05% capital)
2. Normal trading: Buy when edge > 3%, use Kelly criterion
3. Take profit: Sell when edge drops below -8%
4. Hold winners: If likely to win, hold until market ends
"""

from typing import Dict, Tuple, Optional
from dataclasses import dataclass


@dataclass
class TradingThresholds:
    """Trading decision thresholds."""

    # Buy thresholds
    buy_edge_threshold: float = 0.067  # 3% edge required to buy
    strong_buy_edge: float = 0.10  # 10%+ edge = strong buy signal

    # Sell thresholds - More conservative to avoid selling winners early
    sell_edge_threshold: float = -0.05  # Only sell if market is 5%+ higher than our prob
    take_profit_edge: float = -0.1  # Consider selling at -10% edge late in week

    # Position sizing
    early_week_lottery_allocation: float = 0.0005  # 0.05% per lottery ticket
    max_kelly_fraction: float = 0.25  # Cap Kelly at 25% of bankroll

    # Early market lottery targets
    lottery_market_progress_cutoff: float = 0.3  # Only first 30% of market period
    lottery_low_bracket_min: int = 200  # Low bracket: 200-300 tweets
    lottery_low_bracket_max: int = 300
    lottery_high_bracket_min: int = 650  # High bracket: 650+ tweets
    lottery_max_allocation_pct: float = 0.05  # Max 5% of market bankroll in lottery tickets
    stop_loss_drawdown: float = 0.25  # Sell if price falls 25% below cost

    # Winner holding logic - More lenient to hold positions longer
    likely_winner_hold_threshold: float = 0.55  # Hold if >55% prob (was 70%)
    likely_winner_min_edge: float = -0.15  # Hold winners even with -15% edge

    # Acceleration-based profit taking
    acceleration_sell_threshold: float = 1.5  # tweets/hr change to trigger
    acceleration_profit_min_pct: float = 0.3  # need 30%+ gain before forced take-profit


class EnhancedTradingLogic:
    """Implements improved trading decision logic."""

    def __init__(self, thresholds: Optional[TradingThresholds] = None):
        self.t = thresholds or TradingThresholds()

    def should_buy(
        self,
        edge: float,
        our_prob: float,
        price: float,
        week_progress: float,
    ) -> Tuple[bool, str, str]:
        """
        Determine if we should buy this bracket.

        Returns:
            (should_buy, action, timing)
        """
        # Must have positive edge and meet threshold
        if edge < self.t.buy_edge_threshold:
            return False, "PASS", "WATCH"

        # Strong buy signal
        if edge >= self.t.strong_buy_edge:
            return True, "STRONG BUY", "NOW"

        # Good buy signal
        if edge >= self.t.buy_edge_threshold:
            if week_progress < 0.3:
                return True, "BUY", "EARLY"
            elif week_progress < 0.7:
                return True, "BUY", "NOW"
            else:
                return True, "BUY", "NOW"

        return False, "PASS", "WATCH"

    def should_sell(
        self,
        edge: float,
        our_prob: float,
        shares_owned: float,
        week_progress: float,
        is_likely_winner: bool,
        price: float,
        average_cost: Optional[float],
        velocity_spike: bool,
        market_implied_prob: Optional[float],
        acceleration: float,
        is_low_bracket: bool,
        is_high_bracket: bool,
    ) -> Tuple[bool, str, str, float]:
        """
        Determine if we should sell this position.

        Returns:
            (should_sell, action, timing, shares_to_sell)
        """
        if shares_owned <= 0:
            return False, "HOLD", "WATCH", 0

        # Immediate take-profit during velocity spikes for any low bracket
        if velocity_spike and market_implied_prob is not None:
            if market_implied_prob > our_prob + 0.15:
                return True, "TAKE PROFIT (SPIKE)", "NOW", shares_owned

        profit_pct = None
        if average_cost and average_cost > 0 and price is not None:
            profit_pct = (price - average_cost) / average_cost

        # Price-based stop loss regardless of edge
        if average_cost and average_cost > 0 and price is not None:
            drawdown = 1 - (price / average_cost)
            if drawdown >= self.t.stop_loss_drawdown:
                return True, "STOP LOSS", "NOW", shares_owned

        # Trailing take-profit using acceleration cues
        if (
            profit_pct is not None
            and profit_pct >= self.t.acceleration_profit_min_pct
        ):
            if is_low_bracket and acceleration >= self.t.acceleration_sell_threshold:
                return True, "LOCK PROFIT (ACCEL↑)", "NOW", shares_owned
            if is_high_bracket and acceleration <= -self.t.acceleration_sell_threshold:
                return True, "LOCK PROFIT (ACCEL↓)", "NOW", shares_owned

        # Hold likely winners unless edge is terrible
        if is_likely_winner and our_prob > self.t.likely_winner_hold_threshold:
            if edge < self.t.likely_winner_min_edge:
                # Even winners need to be sold if edge is too negative
                return True, "SELL (BAD EDGE)", "NOW", shares_owned
            # Hold the winner
            return False, "HOLD (LIKELY WINNER)", "HOLD", 0

        # Sell if edge drops significantly
        if edge < self.t.sell_edge_threshold:
            return True, "STRONG SELL", "NOW", shares_owned

        # Consider taking profit if edge turned slightly negative
        if edge < self.t.take_profit_edge and week_progress > 0.5:
            return True, "TAKE PROFIT", "NOW", shares_owned * 0.5  # Sell half

        # Hold for now
        return False, "HOLD", "WATCH", 0

    def is_lottery_ticket(
        self,
        min_tweets: int,
        max_tweets: Optional[int],
        price: float,
        week_progress: float,
        predicted_total: float,
        duration_ratio: float,
    ) -> bool:
        """
        Check if this is a valid lottery ticket opportunity.

        Lottery tickets are:
        - Very low probability (cheap)
        - Extreme outcomes (200-300 or 650+)
        - Only in early week (< 30% progress)
        """
        if week_progress >= self.t.lottery_market_progress_cutoff:
            return False

        # Must be cheap (< 1 cent)
        if price >= 0.01:
            return False

        duration_ratio = max(duration_ratio, 0.2)  # prevent extreme scaling
        low_min = int(self.t.lottery_low_bracket_min * duration_ratio)
        low_max = int(self.t.lottery_low_bracket_max * duration_ratio)
        high_min = int(self.t.lottery_high_bracket_min * duration_ratio)

        predicted_high_cutoff = 400 * duration_ratio
        predicted_low_cutoff = 600 * duration_ratio

        # Low bracket scaled by market duration
        is_low_bracket = (
            min_tweets >= low_min
            and max_tweets is not None
            and max_tweets <= low_max
            and predicted_total > predicted_high_cutoff  # Only if we're predicting much higher
        )

        # High bracket scaled by market duration
        is_high_bracket = (
            min_tweets >= high_min
            and predicted_total < predicted_low_cutoff  # Only if we're predicting much lower
        )

        return is_low_bracket or is_high_bracket

    def calculate_position_size(
        self,
        edge: float,
        price: float,
        kelly_fraction: float,
        bankroll: float,
        is_lottery: bool,
    ) -> float:
        """
        Calculate position size in USD.

        For lottery tickets: Fixed 0.05% of bankroll
        For normal trades: Kelly criterion based on edge
        """
        if is_lottery:
            return bankroll * self.t.early_week_lottery_allocation

        # Use Kelly criterion, capped at max_kelly_fraction
        kelly_fraction = min(kelly_fraction, self.t.max_kelly_fraction)
        return kelly_fraction * bankroll
