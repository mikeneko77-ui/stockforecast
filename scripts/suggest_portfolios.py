#!/usr/bin/env python3
"""
Portfolio Suggester — Chronos予測ベースのポートフォリオ最適化
============================================================
Supabase の forecasts テーブルから最新の予測を読み、
予算 + 目標リターンレンジに基づいて最適ポートフォリオを提案。

複数の戦略でポートフォリオ候補を生成し、Supabaseに保存。

Strategies:
  - max_sharpe:     シャープレシオ最大化
  - min_variance:   最小分散 (目標リターン以上)
  - target_return:  目標リターンに最も近い
  - equal_weight:   均等配分 (ベンチマーク)
  - max_return:     リターン最大化 (リスク許容度内)

Usage:
    python suggest_portfolios.py \
        --budget 100000 \
        --target-return-min 2.0 \
        --target-return-max 15.0 \
        --strategies max_sharpe min_variance target_return equal_weight
"""

import json
import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from itertools import combinations

import numpy as np
import pandas as pd
from scipy.optimize import minimize

try:
    from supabase import create_client
    HAS_SUPABASE = True
except ImportError:
    HAS_SUPABASE = False

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════
# Supabase
# ══════════════════════════════════════════════
def get_supabase():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY")
    if not url or not key:
        logger.error("SUPABASE_URL / SUPABASE_KEY not set")
        return None
    return create_client(url, key)


# ══════════════════════════════════════════════
# 1. Load forecasts from Supabase
# ══════════════════════════════════════════════
def load_latest_forecasts(sb) -> pd.DataFrame:
    """
    Load the latest run's forecasts for all active symbols.
    Returns DataFrame with columns: symbol, target_date, close, mean, upper, lower, p25, p75
    """
    # Get the latest run_date
    latest = (
        sb.table("forecasts")
        .select("run_date")
        .order("run_date", desc=True)
        .limit(1)
        .execute()
    )
    if not latest.data:
        logger.error("No forecasts found in Supabase")
        return pd.DataFrame()

    run_date = latest.data[0]["run_date"]
    logger.info(f"Loading forecasts from run_date={run_date}")

    # Fetch all forecasts for this run
    result = (
        sb.table("forecasts")
        .select("symbol, target_date, close, mean, upper, lower, p25, p75")
        .eq("run_date", run_date)
        .order("target_date")
        .execute()
    )

    df = pd.DataFrame(result.data)
    logger.info(f"  Loaded {len(df)} forecast rows for {df['symbol'].nunique()} symbols")
    return df


def load_stock_prices(sb, symbols: list[str]) -> dict:
    """Load current prices from stocks table or latest forecast close."""
    result = sb.table("stocks").select("symbol, name").in_("symbol", symbols).execute()
    stock_info = {r["symbol"]: r["name"] for r in result.data}
    return stock_info


# ══════════════════════════════════════════════
# 2. Compute Expected Returns & Covariance
# ══════════════════════════════════════════════
def compute_return_metrics(forecasts_df: pd.DataFrame) -> dict:
    """
    From Chronos forecasts, compute per-symbol:
      - expected_return: (mean_final - close) / close
      - risk (σ): estimated from (upper - lower) / (2 * 1.645) → daily σ → annualized
      - downside: (lower_final - close) / close
    """
    symbols = forecasts_df["symbol"].unique()
    metrics = {}

    for sym in symbols:
        df = forecasts_df[forecasts_df["symbol"] == sym].sort_values("target_date")
        if df.empty:
            continue

        close = df["close"].iloc[0]
        horizon = len(df)

        # Final day forecasts
        final = df.iloc[-1]
        mean_final = final["mean"]
        upper_final = final["upper"]
        lower_final = final["lower"]

        # Expected return over horizon
        exp_return = (mean_final - close) / close * 100  # %

        # Risk estimation from prediction interval width
        # 90% CI: upper - lower ≈ 2 * 1.645 * σ * sqrt(T) * S0
        ci_width = upper_final - lower_final
        daily_vol = ci_width / (2 * 1.645 * np.sqrt(horizon) * close)
        annual_vol = daily_vol * np.sqrt(252) * 100  # %

        # Downside risk (worst case at p5)
        downside = (lower_final - close) / close * 100  # %

        metrics[sym] = {
            "close": close,
            "expected_return": exp_return,     # % over horizon
            "annual_volatility": annual_vol,   # annualized σ %
            "downside_return": downside,       # % p5
            "upside_return": (upper_final - close) / close * 100,  # % p95
            "horizon": horizon,
        }

    return metrics


def estimate_covariance(forecasts_df: pd.DataFrame) -> pd.DataFrame:
    """
    Estimate return covariance from daily forecast means.
    Using daily changes in forecast means as proxy for correlated movements.
    """
    pivot = forecasts_df.pivot_table(
        index="target_date", columns="symbol", values="mean"
    )
    # Daily returns from forecast means
    returns = pivot.pct_change().dropna()
    cov = returns.cov() * 252  # annualize
    return cov


# ══════════════════════════════════════════════
# 3. Portfolio Optimization
# ══════════════════════════════════════════════
def optimize_portfolio(
    expected_returns: dict,
    cov_matrix: pd.DataFrame,
    budget: float,
    current_prices: dict,
    strategy: str = "max_sharpe",
    target_return_min: float = None,
    target_return_max: float = None,
    risk_free_rate: float = 0.04,  # 4% annual
) -> dict | None:
    """
    Mean-variance optimization.
    Returns portfolio weights, shares, and metrics.
    """
    symbols = [s for s in expected_returns if s in cov_matrix.columns]
    if len(symbols) < 2:
        logger.warning("Need at least 2 symbols for optimization")
        return None

    n = len(symbols)
    mu = np.array([expected_returns[s]["expected_return"] for s in symbols])
    cov = cov_matrix.loc[symbols, symbols].values
    prices = np.array([current_prices[s] for s in symbols])

    # Annualize expected returns (forecasts are over ~60 days)
    horizon_days = expected_returns[symbols[0]]["horizon"]
    mu_annual = mu * (252 / horizon_days)

    # Risk-free rate adjustment for Sharpe
    rf = risk_free_rate * 100  # convert to %

    def portfolio_return(w):
        return np.dot(w, mu_annual)

    def portfolio_risk(w):
        return np.sqrt(np.dot(w, np.dot(cov * 10000, w)))  # cov is in decimal, mu in %

    def neg_sharpe(w):
        ret = portfolio_return(w)
        risk = portfolio_risk(w)
        return -(ret - rf) / (risk + 1e-10)

    # Constraints
    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]

    if target_return_min is not None:
        # Annualize target
        tr_min_annual = target_return_min * (252 / horizon_days)
        constraints.append({
            "type": "ineq",
            "fun": lambda w: portfolio_return(w) - tr_min_annual
        })

    if target_return_max is not None:
        tr_max_annual = target_return_max * (252 / horizon_days)
        constraints.append({
            "type": "ineq",
            "fun": lambda w: tr_max_annual - portfolio_return(w)
        })

    # Bounds: 0% to 40% per asset (diversification constraint)
    bounds = [(0.0, 0.40)] * n

    # Objective function by strategy
    if strategy == "max_sharpe":
        objective = neg_sharpe
    elif strategy == "min_variance":
        objective = portfolio_risk
    elif strategy == "max_return":
        objective = lambda w: -portfolio_return(w)
    elif strategy == "target_return":
        # Minimize deviation from midpoint of target range
        mid = ((target_return_min or 0) + (target_return_max or 0)) / 2
        mid_annual = mid * (252 / horizon_days)
        objective = lambda w: (portfolio_return(w) - mid_annual) ** 2
    elif strategy == "equal_weight":
        # No optimization needed
        weights = np.ones(n) / n
        ret = portfolio_return(weights)
        risk = portfolio_risk(weights)
        sharpe = (ret - rf) / (risk + 1e-10)

        # Convert to shares
        allocations = weights * budget
        shares = np.floor(allocations / prices).astype(int)
        actual_value = np.sum(shares * prices)

        return {
            "symbols": symbols,
            "weights": weights.tolist(),
            "shares": shares.tolist(),
            "expected_return": float(ret / (252 / horizon_days)),  # back to horizon
            "expected_risk": float(risk),
            "sharpe_ratio": float(sharpe),
            "total_value": float(actual_value),
            "strategy": strategy,
        }
    else:
        logger.error(f"Unknown strategy: {strategy}")
        return None

    # Initial guess: equal weight
    w0 = np.ones(n) / n

    result = minimize(
        objective, w0,
        method="SLSQP",
        bounds=bounds,
        constraints=constraints,
        options={"maxiter": 1000, "ftol": 1e-12},
    )

    if not result.success:
        logger.warning(f"Optimization did not converge for {strategy}: {result.message}")
        # Fall back to equal weight
        weights = np.ones(n) / n
    else:
        weights = result.x

    # Normalize
    weights = np.maximum(weights, 0)
    weights /= weights.sum()

    ret = portfolio_return(weights)
    risk = portfolio_risk(weights)
    sharpe = (ret - rf) / (risk + 1e-10)

    # Convert weights to shares (integer, fitting budget)
    allocations = weights * budget
    shares = np.floor(allocations / prices).astype(int)
    actual_value = float(np.sum(shares * prices))

    return {
        "symbols": symbols,
        "weights": weights.tolist(),
        "shares": shares.tolist(),
        "expected_return": float(ret / (252 / horizon_days)),  # convert back to horizon period
        "expected_risk": float(risk),
        "sharpe_ratio": float(sharpe),
        "total_value": actual_value,
        "strategy": strategy,
    }


# ══════════════════════════════════════════════
# 4. Save to Supabase
# ══════════════════════════════════════════════
def save_portfolio_to_supabase(
    sb, portfolio_result: dict, budget: float,
    target_min: float, target_max: float,
    stock_info: dict, forecasts_df: pd.DataFrame,
) -> str | None:
    """Save portfolio + holdings + forecasts to Supabase. Returns portfolio_id."""
    if sb is None:
        return None

    strategy = portfolio_result["strategy"]
    symbols = portfolio_result["symbols"]
    weights = portfolio_result["weights"]
    shares = portfolio_result["shares"]

    # 1. Insert portfolio
    portfolio_row = {
        "name": f"{strategy.replace('_', ' ').title()} — ¥{budget:,.0f}",
        "description": f"Auto-generated by suggest_portfolios.py ({strategy})",
        "budget": budget,
        "target_return_min": target_min,
        "target_return_max": target_max,
        "strategy": strategy,
        "expected_return": portfolio_result["expected_return"],
        "expected_risk": portfolio_result["expected_risk"],
        "sharpe_ratio": portfolio_result["sharpe_ratio"],
        "total_value": portfolio_result["total_value"],
        "is_active": True,
    }
    try:
        res = sb.table("portfolios").insert(portfolio_row).execute()
        portfolio_id = res.data[0]["id"]
        logger.info(f"  Created portfolio: {portfolio_id} ({strategy})")
    except Exception as e:
        logger.error(f"  Failed to create portfolio: {e}")
        return None

    # 2. Insert holdings
    holdings = []
    for i, sym in enumerate(symbols):
        if shares[i] <= 0:
            continue
        holdings.append({
            "portfolio_id": portfolio_id,
            "symbol": sym,
            "shares": int(shares[i]),
            "weight": float(weights[i]),
            "allocated_value": float(weights[i] * budget),
        })

    if holdings:
        try:
            sb.table("portfolio_holdings").upsert(
                holdings, on_conflict="portfolio_id,symbol"
            ).execute()
            logger.info(f"  Saved {len(holdings)} holdings")
        except Exception as e:
            logger.error(f"  Failed to save holdings: {e}")

    # 3. Compute & save portfolio-level forecasts
    # Aggregate across held symbols
    held_symbols = {h["symbol"]: h["shares"] for h in holdings}
    pf_df = forecasts_df[forecasts_df["symbol"].isin(held_symbols.keys())].copy()

    if not pf_df.empty:
        pf_forecasts = []
        base_value = portfolio_result["total_value"]

        for target_date in pf_df["target_date"].unique():
            day_df = pf_df[pf_df["target_date"] == target_date]
            v_mean = sum(
                day_df[day_df["symbol"] == s]["mean"].iloc[0] * sh
                for s, sh in held_symbols.items()
                if not day_df[day_df["symbol"] == s].empty
            )
            v_upper = sum(
                day_df[day_df["symbol"] == s]["upper"].iloc[0] * sh
                for s, sh in held_symbols.items()
                if not day_df[day_df["symbol"] == s].empty
            )
            v_lower = sum(
                day_df[day_df["symbol"] == s]["lower"].iloc[0] * sh
                for s, sh in held_symbols.items()
                if not day_df[day_df["symbol"] == s].empty
            )

            pf_forecasts.append({
                "portfolio_id": portfolio_id,
                "target_date": target_date,
                "value_mean": float(v_mean),
                "value_upper": float(v_upper),
                "value_lower": float(v_lower),
                "return_mean": float((v_mean - base_value) / base_value * 100),
                "return_upper": float((v_upper - base_value) / base_value * 100),
                "return_lower": float((v_lower - base_value) / base_value * 100),
            })

        try:
            BATCH = 50
            for start in range(0, len(pf_forecasts), BATCH):
                sb.table("portfolio_forecasts").upsert(
                    pf_forecasts[start:start+BATCH],
                    on_conflict="portfolio_id,target_date"
                ).execute()
            logger.info(f"  Saved {len(pf_forecasts)} portfolio forecast rows")
        except Exception as e:
            logger.error(f"  Failed to save portfolio forecasts: {e}")

    return portfolio_id


# ══════════════════════════════════════════════
# 5. Main
# ══════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="Portfolio Suggester")
    parser.add_argument("--budget", type=float, required=True, help="Investment budget (USD)")
    parser.add_argument("--target-return-min", type=float, default=None, help="Min expected return (%)")
    parser.add_argument("--target-return-max", type=float, default=None, help="Max expected return (%)")
    parser.add_argument(
        "--strategies", nargs="+",
        default=["max_sharpe", "min_variance", "target_return", "equal_weight"],
        help="Optimization strategies to run",
    )
    parser.add_argument("--output", default="forecasts/suggestions.json")
    parser.add_argument("--no-supabase", action="store_true")
    args = parser.parse_args()

    sb = None
    if not args.no_supabase and HAS_SUPABASE:
        sb = get_supabase()

    if sb is None:
        logger.error("Supabase connection required. Set SUPABASE_URL and SUPABASE_KEY.")
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("PORTFOLIO SUGGESTER")
    logger.info(f"  Budget:        ${args.budget:,.0f}")
    logger.info(f"  Target Return: {args.target_return_min}% ~ {args.target_return_max}%")
    logger.info(f"  Strategies:    {args.strategies}")
    logger.info("=" * 60)

    # Load forecasts
    forecasts_df = load_latest_forecasts(sb)
    if forecasts_df.empty:
        logger.error("No forecasts available. Run train_and_forecast.py first.")
        sys.exit(1)

    symbols = forecasts_df["symbol"].unique().tolist()
    stock_info = load_stock_prices(sb, symbols)

    # Compute metrics
    metrics = compute_return_metrics(forecasts_df)
    logger.info("\nPer-symbol metrics:")
    for sym, m in metrics.items():
        logger.info(
            f"  {sym:6s} | ${m['close']:>8.2f} | "
            f"E[R]={m['expected_return']:>+6.2f}% | "
            f"σ={m['annual_volatility']:.1f}% | "
            f"↓{m['downside_return']:.1f}% ↑+{m['upside_return']:.1f}%"
        )

    # Estimate covariance
    cov_matrix = estimate_covariance(forecasts_df)
    current_prices = {s: metrics[s]["close"] for s in metrics}

    # Run each strategy
    results = []
    for strategy in args.strategies:
        logger.info(f"\n─── Strategy: {strategy} ───")

        result = optimize_portfolio(
            expected_returns=metrics,
            cov_matrix=cov_matrix,
            budget=args.budget,
            current_prices=current_prices,
            strategy=strategy,
            target_return_min=args.target_return_min,
            target_return_max=args.target_return_max,
        )

        if result is None:
            logger.warning(f"  Skipped {strategy}")
            continue

        # Log result
        logger.info(f"  E[Return]: {result['expected_return']:>+.2f}%")
        logger.info(f"  Risk (σ):  {result['expected_risk']:.2f}%")
        logger.info(f"  Sharpe:    {result['sharpe_ratio']:.3f}")
        logger.info(f"  Value:     ${result['total_value']:,.0f} / ${args.budget:,.0f}")
        logger.info(f"  Allocation:")
        for i, sym in enumerate(result["symbols"]):
            if result["shares"][i] > 0:
                logger.info(
                    f"    {sym:6s}: {result['shares'][i]:>4} shares "
                    f"({result['weights'][i]*100:>5.1f}%)"
                )

        # Save to Supabase
        portfolio_id = save_portfolio_to_supabase(
            sb, result, args.budget,
            args.target_return_min, args.target_return_max,
            stock_info, forecasts_df,
        )
        result["portfolio_id"] = portfolio_id
        results.append(result)

    # Save JSON summary
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump({
            "generated_at": datetime.now().isoformat(),
            "budget": args.budget,
            "target_return_range": [args.target_return_min, args.target_return_max],
            "suggestions": results,
        }, f, indent=2, default=str)

    logger.info(f"\n{'═' * 60}")
    logger.info(f"Generated {len(results)} portfolio suggestions")
    logger.info(f"Saved to: {output_path}")
    logger.info(f"{'═' * 60}")


if __name__ == "__main__":
    main()
