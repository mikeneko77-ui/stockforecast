001_schema.sql


-- 1. 銘柄マスタ
CREATE TABLE IF NOT EXISTS stocks (
    symbol      TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    sector      TEXT,
    currency    TEXT DEFAULT 'USD',
    is_active   BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);

-- 2. Chronos 日次予測結果
CREATE TABLE IF NOT EXISTS forecasts (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    run_date    DATE NOT NULL,
    target_date DATE NOT NULL,
    symbol      TEXT NOT NULL REFERENCES stocks(symbol),
    close       NUMERIC NOT NULL,
    mean        NUMERIC NOT NULL,
    upper       NUMERIC NOT NULL,
    lower       NUMERIC NOT NULL,
    p25         NUMERIC,
    p75         NUMERIC,
    model       TEXT DEFAULT 'chronos-t5-small',
    created_at  TIMESTAMPTZ DEFAULT now(),

    UNIQUE (target_date, symbol)
);

CREATE INDEX idx_forecasts_symbol ON forecasts(symbol);
CREATE INDEX idx_forecasts_run_date ON forecasts(run_date);
CREATE INDEX idx_forecasts_target_date ON forecasts(target_date);
CREATE INDEX idx_forecasts_symbol_target ON forecasts(symbol, target_date);

-- Seed: 初期銘柄
INSERT INTO stocks (symbol, name, sector) VALUES
    ('AAPL', 'Apple Inc.', 'Technology')
ON CONFLICT (symbol) DO NOTHING;