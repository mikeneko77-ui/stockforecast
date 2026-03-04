import { supabase } from "./supabase";

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

export async function fetchPortfolios(): Promise<PortfolioRow[]> {
  if (!supabase) return [];
  const { data, error } = await supabase
    .from("portfolios")
    .select("*")
    .order("created_at", { ascending: false });
  if (error) throw error;
  return data || [];
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
  throw error;
}
