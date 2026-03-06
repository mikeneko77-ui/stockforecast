import { supabase, isSupabaseConfigured } from "./supabase";

export interface ForecastRow {
  target_date: string;
  symbol: string;
  close: number;
  mean: number;
  upper: number;
  lower: number;
  run_date: string;
  model: string;
}

export async function fetchForecasts(symbol: string): Promise<ForecastRow[]> {
  if (!isSupabaseConfigured) return [];

  const { data, error } = await supabase
    .from("forecasts")
    .select("target_date, symbol, close, mean, upper, lower, run_date, model")
    .eq("symbol", symbol)
    .order("target_date", { ascending: true });

  if (error) throw error;
  return data ?? [];
}
