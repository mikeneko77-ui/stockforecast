import { useState, useEffect, useCallback } from "react";
import {
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  ComposedChart,
} from "recharts";
import { isSupabaseConfigured } from "./lib/supabase";
import {
  fetchPortfolios,
  createPortfolios,
  deletePortfolio,
  type PortfolioRow,
} from "./lib/portfolios";

import { fetchForecasts, type ForecastRow } from "./lib/forecasts";
import { Area } from "recharts";

interface ChartPoint {
  date: string;
  price?: number;
  forecast?: number;
  upper?: number;
  lower?: number;
}

interface StockData {
  ticker: string;
  name: string;
  current_price: number;
  history: { dates: string[]; prices: number[] };
}

export default function App() {
  const [data, setData] = useState<StockData | null>(null);
  const [portofolios, setPortfolios] = useState<PortfolioRow[]>([]);
  const [newName, setNewName] = useState("");
  const [error, setError] = useState("");

  const [forecasts, setForecasts] = useState<ForecastRow[]>([]);

  useEffect(() => {
    // 実績データを forecasts/AAPL.json から取得
    fetch("/forecasts/AAPL.json")
      .then((res) => res.json())
      .then((json) => {
        setData({
          ticker: json.ticker,
          name: json.name,
          current_price: json.current_price,
          history: json.history,
        });
      })
      .catch((e) => setError(`株価データの取得に失敗: ${e.message}`));

    // 予測データを Supabase から取得
    fetchForecasts("AAPL")
      .then(setForecasts)
      .catch((e) => setError(e.message));
  }, []);

  const historyPoints: ChartPoint[] = data
    ? data.history.dates.map((date, i) => ({
        date,
        price: Math.round(data.history.prices[i] * 100) / 100,
      }))
    : [];

  const lastHistory = historyPoints[historyPoints.length - 1];
  const forecastPoints: ChartPoint[] = lastHistory
    ? [
        { date: lastHistory.date, forecast: lastHistory.price },
        ...forecasts.map((f) => ({
          date: f.target_date,
          forecast: Math.round(f.mean * 100) / 100,
          upper: Math.round(f.upper * 100) / 100,
          lower: Math.round(f.lower * 100) / 100,
        })),
      ]
    : [];
  const chartData = [...historyPoints, ...forecastPoints.slice(1)];

  const loadPortfolios = useCallback(async () => {
    try {
      const p = await fetchPortfolios();
      setPortfolios(p);
    } catch (e: any) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    loadPortfolios();
  }, [loadPortfolios]);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    try {
      await createPortfolios(newName.trim());
      setNewName("");
      loadPortfolios();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await deletePortfolio(id);
      loadPortfolios();
    } catch (e: any) {
      setError(e.message);
    }
  };

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b border-gray-200 px-6 py-3">
        <h1 className="font-mono text-lg font-bold text-gray-900">
          STOCK FORECAST
        </h1>
      </header>

      <div className="max-w-4xl mx-auto p-6">
        <div className="bg-white border border-gray-200 rounded-xl p-6 shadow-sm">
          <div className="flex justify-between items-baseline mb-4">
            <div>
              <span className="font-mono font-bold text-gray-900">
                {data?.ticker ?? "Loading..."}
              </span>
              <span className="text-sm text-gray-500 ml-2">{data?.name}</span>
            </div>
            <span className="font-mono text-lg font-bold text-gray-900">
              {data ? `$${data.current_price.toFixed(2)}` : ""}
            </span>
          </div>

          <ResponsiveContainer width="100%" height={400}>
            <ComposedChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="date" tick={{ fontSize: 10 }} interval={14} />
              <YAxis domain={["auto", "auto"]} tick={{ fontSize: 10 }} />
              <Tooltip />
              <Line
                type="monotone"
                dataKey="price"
                stroke="#059669"
                dot={false}
                strokeWidth={2}
                name="実績"
              />
              <Line
                type="monotone"
                dataKey="forecast"
                stroke="#2563eb"
                dot={false}
                strokeWidth={2}
                strokeDasharray="6 3"
                name="予測"
              />
              <Area
                type="monotone"
                dataKey="upper"
                stroke="none"
                fill="#2563eb"
                fillOpacity={0.1}
                name="上限"
              />
              <Area
                type="monotone"
                dataKey="lower"
                stroke="none"
                fill="#2563eb"
                fillOpacity={0.1}
                name="下限"
              />
            </ComposedChart>
          </ResponsiveContainer>
        </div>

        <div className="bg-white border border-gray-200 rounded-xl p-6 shadow-sm">
          <h2 className="font-mono text-sm font-bold text-gray-500 tracking-wider mb-4">
            ポートフォリオ
          </h2>
          {!isSupabaseConfigured && (
            <div className="mb-4 p-3 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-700">
              Supabaseが未設定です。 .env.localにVITE_SUPABASE_URL と
              VITE_SUPABASE_ANON_KEY を設定してください。
            </div>
          )}

          {error && (
            <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-xs text-red-600">
              {error}
            </div>
          )}

          <div className="flex gap-2 mb-4">
            <input
              type="text"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="ポートフォリオ名"
              className="flex-1 border border-gray-200 rounded-lg px-3 py-2 text-sm outline-none focus:border-blue-400"
              onKeyDown={(e) => e.key === "Enter" && handleCreate()}
            />
            <button
              onClick={handleCreate}
              disabled={!isSupabaseConfigured || !newName.trim()}
              className="bg-blue-600 text-white px-4 py-2 rounded-lg text-sm font-semibold hover:bg-blue-700 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
            >
              保存
            </button>
          </div>

          {/* 一覧 */}
          {portofolios.length === 0 ? (
            <p className="text-sm text-gray-400">ポートフォリオがありません</p>
          ) : (
            <div className="space-y-2">
              {portofolios.map((p) => (
                <div
                  key={p.id}
                  className="flex items-center justify-between p-3 rounded-lg bg-gray-50 hover:bg-gray-100 transition-colors"
                >
                  <div>
                    <div className="font-mono text-sm font-semibold text-gray-800">
                      {p.name}
                    </div>
                    <div className="text-xs text-gray-400 font-mono">
                      ${p.budget.toLocaleString()} ·{" "}
                      {new Date(p.created_at).toLocaleDateString()}
                    </div>
                  </div>
                  <button
                    onClick={() => handleDelete(p.id)}
                    className="text-xs text-gray-400 hover:text-red-500 cursor-pointer font-mono"
                  >
                    削除
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
