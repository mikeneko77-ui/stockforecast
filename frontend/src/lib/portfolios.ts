import { supabase } from "./supabase";

export interface HoldingRow {
  symbol: string;
  shares: number;
  weight: number;
  allocated_value: number;
}

export interface PortfolioForecastRow {
  target_date: string;
  value_mean: number;
  value_upper: number;
  value_lower: number;
  return_mean: number;
}

export interface PortfolioRow {
  id: string;
  name: string;
  budget: number;
  strategy: string;
  expected_return: number | null;
  expected_risk: number | null;
  sharpe_ratio: number | null;
  total_value: number | null;
  created_at: string;
}

export async function fetchHoldings(
  portfolioId: string
): Promise<HoldingRow[]> {
  if (!supabase) return [];
  const { data, error } = await supabase
    .from("portfolio_holdings")
    .select("symbol, shares, weight, allocated_value")
    .eq("portfolio_id", portfolioId);
  if (error) throw error;
  return data ?? [];
}

export async function fetchPortfolios(): Promise<PortfolioRow[]> {
  if (!supabase) return [];
  const { data, error } = await supabase
    .from("portfolios")
    .select("*")
    .order("created_at", { ascending: false });
  if (error) throw error;
  return data || [];
}

export async function fetchPortfolioForecasts(
  portfolioId: string
): Promise<PortfolioForecastRow[]> {
  if (!supabase) return [];
  const { data, error } = await supabase
    .from("portfolio_forecasts")
    .select("target_date, value_mean, value_upper, value_lower, return_mean")
    .eq("portfolio_id", portfolioId)
    .order("target_date", { ascending: true });
  if (error) throw error;
  return data ?? [];
}

export async function createPortfolios(
  name: string,
  budget: number = 100000
): Promise<PortfolioRow> {
  if (!supabase) throw new Error("Supabase not configured");
  const { data, error } = await supabase
    .from("portfolios")
    .insert({ name, budget })
    .select()
    .single();
  if (error) throw error;
  return data;
}

export async function deletePortfolio(id: string): Promise<void> {
  if (!supabase) throw new Error("Supabase not configured");
  const { error } = await supabase.from("portfolios").delete().eq("id", id);
  if (error) throw error;
}
