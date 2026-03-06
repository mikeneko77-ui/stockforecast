#!/usr/bin/env python3
import os
try:
    from supabase import create_client
    HAS_SUPABASE = True
except:
    HAS_SUPABASE = False

import json
import argparse
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
import torch

try:
    from chronos import ChronosPipeline, ChronosBoltPipeline
    HAS_CHRONOS = True
except ImportError:
    HAS_CHRONOS = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CHRONOS_MODELS = {
    "tiny":       "amazon/chronos-t5-tiny",
    "small":      "amazon/chronos-t5-small",
    "base":       "amazon/chronos-t5-base",
    "bolt-tiny":  "amazon/chronos-bolt-tiny",
    "bolt-small": "amazon/chronos-bolt-small",
    "bolt-base":  "amazon/chronos-bolt-base",
}

def get_supabase():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY")
    if not url or not key:
        logger.warning("SUPABASE_URL / SUPABASE_KEY not set")
        return None
    return create_client(url, key)

def ensure_stock_exists(sb, symbol: str, name: str):
    if sb is None:
        return
    try:
        sb.table("stocks").upsert(
            {"symbol": symbol, "name": name, "is_active": True},
            on_conflict="symbol"
        ).execute()
    except Exception as e:
        logger.warning(f"  Stock upsert waiting for {symbol}: {e}")

def upsert_forecasts_to_supabase(
    sb, symbol: str, run_date: str, close_price: float,
    quantiles: dict, horizon: int, model_name: str):
    if sb is None:
        return
    base = pd.Timestamp(run_date)
    target_dates = pd.bdate_range(start=base + pd.offsets.BDay(1), periods=horizon)

    rows = []
    for i, td in enumerate(target_dates):
        rows.append({
            "run_date": run_date,
            "target_date": td.strftime('%Y-%m-%d'),
            "symbol": symbol,
            "close": float(close_price),
            "mean": float(quantiles["mean"][i]),
            "upper": float(quantiles["upper"][i]),
            "lower": float(quantiles["lower"][i]),
            "p25": float(quantiles["p25"][i]),
            "p75": float(quantiles["p75"][i]),
            "model": model_name,
        })

    BATCH_SIZE = 50
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start:start + BATCH_SIZE]
        try:
            sb.table("forecasts").upsert(
                batch, on_conflict="target_date,symbol"
            ).execute()
        except Exception as e:
            logger.error(f"  Supabase upsert error for {symbol}: {e}")
            return
    logger.info(f"  Supabase: upserted {len(rows)} forecast rows for {symbol}")


def fetch_stock_data(ticker: str, days: int = 730) -> pd.DataFrame | None:
    end = datetime.now()
    start = end - timedelta(days=days)
    logger.info(f"Fetching {ticker}: {start.date()} -> {end.date()}")
    try:
        df = yf.download(ticker, start=start.strftime("%Y-%m-%d"),
                         end=end.strftime("%Y-%m-%d"), progress=False, auto_adjust=True)
        if df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df[["Close"]].dropna()
        df.index = pd.DatetimeIndex(df.index)
        df = df.asfreq("B", method="ffill")
        df.columns = ["close"]
        logger.info(f"  {ticker}: {len(df)} days")
        return df
    except Exception as e:
        logger.error(f"Failed {ticker}: {e}")
        return None

def load_chronos(model_size: str = "bolt-small"):
    if not HAS_CHRONOS:
        logger.warning("chronos-forecasting not installed")
        return None, False
    model_id = CHRONOS_MODELS.get(model_size)
    if not model_id:
        logger.error(f"Unknown model: {model_size}")
        return None, False
    
    t0 = time.time()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    is_bolt = "bolt" in model_size
    cls = ChronosBoltPipeline if is_bolt else ChronosPipeline
    pipeline = cls.from_pretrained(model_id, device_map=device, torch_dtype=torch.float32)
    logger.info(f" Loaded in {time.time()-t0:.1f}s (devuce={device})")
    return pipeline, is_bolt

def chronos_forecast(pipeline, prices: np.ndarray, horizon: int, num_samples: int, is_bolt: bool = False):
    context = torch.tensor(prices.astype(np.float32)).unsqueeze(0)
    if is_bolt:
        out = pipeline.predict(context, prediction_length=horizon)[0].numpy()
        return {
            "lower": out[0], "p25": out[1], "median": out[4],
            "p75": out[7], "upper": out[8], "mean": out[4],
        }
    else:
        samples = pipeline.predict(context, prediction_length=horizon,
        num_samples=num_samples)[0].numpy()

        quantiles = {}
        for q, label in [(0.05, "lower"), (0.25, "p25"), (0.5, "median"),
                         (0.75, "p75"), (0.95, "upper")]:
            quantiles[label] = np.quantile(samples, q, axis=0)
        quantiles["mean"] = np.mean(samples, axis=0)
        return quantiles

def gbm_fallback(prices: np.ndarray, horizon: int, num_sims: int = 500):
    """Chronosが使えない場合のフォールバック（幾何ブラウン運動）"""
    log_ret = np.diff(np.log(prices))
    mu, sigma = np.mean(log_ret), np.std(log_ret, ddof=1)
    last = prices[-1]
    Z = np.random.standard_normal((num_sims, horizon))
    paths = last * np.cumprod(np.exp((mu - 0.5*sigma**2) + sigma * Z), axis=1)
    quantiles = {}
    for q, label in [(0.05, "lower"), (0.25, "p25"), (0.5, "median"),
                     (0.75, "p75"), (0.95, "upper")]:
        quantiles[label] = np.quantile(paths, q, axis=0)
    quantiles["mean"] = np.mean(paths, axis=0)
    return quantiles

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/tickers.json")
    parser.add_argument("--output", default="forecasts")
    parser.add_argument("--model-size", default="bolt-small", choices=list(CHRONOS_MODELS.keys()))
    parser.add_argument("--fallback-only", action="store_true")
    parser.add_argument("--no-supabase", action="store_true")
    args = parser.parse_args()

    sb = None
    if not args.no_supabase and HAS_SUPABASE:
        sb = get_supabase()
        if sb:
            logger.info("Supabase: connected")

    with open(args.config) as f:
        config = json.load(f)

    tickers_cfg = config["tickers"]
    fc_cfg = config["forecast_config"]
    horizon = fc_cfg["forecast_horizon"]
    num_samples = fc_cfg.get("num_samples", 500)    
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load Chronos
    pipeline, is_bolt = None, False
    if not args.fallback_only:
        pipeline, is_bolt = load_chronos(args.model_size)

    model_name = f"Chronos ({args.model_size})" if pipeline else "GBM-MonteCarlo"

    logger.info("=" * 60)
    logger.info(f"FORECAST PIPELINE")
    logger.info(f"  Tickers: {list(tickers_cfg.keys())}")
    logger.info(f"  Model:   {model_name}")
    logger.info(f"  Horizon: {horizon} days")
    logger.info("=" * 60)

    for ticker, info in config["tickers"].items():
        df = fetch_stock_data(ticker, config["forecast_config"]["history_days"])
        if df is None:
            continue
        
        prices = df["close"].values
        current_price = float(prices[-1])
        run_date = df.index[-1].strftime("%Y-%m-%d")

        # Forecast
        t0 = time.time()
        if pipeline is not None:
            try:
                quantiles = chronos_forecast(pipeline, prices, horizon, num_samples, is_bolt)
                used_model = model_name
            except Exception as e:
                logger.error(f"Chronos failef for {ticker}: {e}")
                quantiles = gbm_fallback(prices, horizon, num_samples)
                used_model = "GBM-MonteCarlo"
        else:
            quantiles = gbm_fallback(prices, horizon, num_samples)
            used_model = "GBM-MonteCarlo"
        
        elapsed = time.time() - t0
        logger.info(f"  {ticker}: {elapsed:.2f}s ({used_model})")

        # Save JSON
        forecast = {"days": list(range(1, horizon + 1))}
        for key in ["mean", "upper", "lower", "p25", "p75", "median"]:
            if key in quantiles:
                forecast[key] = quantiles[key].tolist()

        output = {
            "ticker": ticker,
            "name": info["name"],
            "shares": info["shares"],
            "current_price": current_price,
            "model": used_model,
            "generated_at": datetime.now().isoformat(),
            "forecast": forecast,
            "history": {
                "dates": df.index.strftime("%Y-%m-%d").tolist()[-90:],
                "prices": df["close"].tolist()[-90:],
            },
        }

        if sb:
            ensure_stock_exists(sb, ticker, info["name"])
            upsert_forecasts_to_supabase(
                sb, ticker, run_date, current_price,
                quantiles, horizon, used_model
            )

        path = output_dir / f"{ticker}.json"
        with open(path, "w") as f:
            json.dump(output, f, indent=2)
        logger.info(f"Saved {path}")

        exp_ret = (quantiles["mean"][-1] - current_price) / current_price * 100
        logger.info(f"  {ticker} | ${current_price:.2f} → E[${quantiles['mean'][-1]:.2f}] | E[R]={exp_ret:+.2f}%")

if __name__ == "__main__":
    main()