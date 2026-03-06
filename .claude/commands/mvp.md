# MVP Navigation

kadai5-2 プロジェクト（Stock Forecast）の MVP ステップガイドです。

引数が指定された場合（例: `/mvp 4`）、そのMVPの手順を案内してください。
引数がない場合は、現在のコードベースを確認してどのMVPまで完了しているか判断し、次のMVPの手順を案内してください。

引数: $ARGUMENTS

## MVP一覧

### MVP0: プロジェクト初期化 & CI/CDセットアップ
- ディレクトリ構成、Git初期化、.gitignore
- Python requirements.txt + pip install (conda activate tsa)
- Frontend: Vite + React + TypeScript + Tailwind CSS + Recharts
- Supabase プロジェクト作成、環境変数テンプレート
- GitHub Actions 雛形 (.github/workflows/forecast.yml)
- Vercel hosting (GitHub連携、Root Directory = frontend)

### MVP1: AAPL株価を取得して表示
- config/tickers.json (AAPLのみ)
- scripts/train_and_forecast.py (株価取得 + JSON出力のみ)
- Frontend: Recharts ComposedChart で株価時系列チャート表示

### MVP2: ポートフォリオをSupabaseに登録
- supabase/migrations/001_schema.sql (stocks, portfolios, portfolio_holdings)
- frontend/src/lib/supabase.ts (Supabase クライアント)
- frontend/src/lib/portfolios.ts (CRUD: fetch, create, delete)
- UI: ポートフォリオ名入力フォーム + 一覧表示 + 削除

### MVP3: GitHub ActionsでAAPLをforecasting
- train_and_forecast.py に Chronos-Bolt 推論追加
- GBMフォールバック (Chronos未使用時)
- forecast.yml 更新 (workflow_dispatch, pip cache, HF cache, commit results)
- ローカル + GitHub Actions で動作確認

### MVP4: 予測結果をSupabaseに保存しUIに表示
- forecasts テーブル (001_schema.sql に含む、(target_date, symbol) UNIQUE)
- train_and_forecast.py に Supabase upsert 追加
- GitHub Actions に SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY を secrets で設定
- Frontend: Supabase から forecasts を読み込み、実績=実線 + 予測=破線 で表示

### MVP5: ポートフォリオの銘柄に対して予測結果を保存・表示
- config/tickers.json を5銘柄に拡張
- portfolio_holdings, portfolio_forecasts テーブル
- scripts/suggest_portfolios.py (Supabaseから予測読み込み → 最適化 → 保存)
- GitHub Actions に suggest ステップ追加
- Frontend: ポートフォリオ構成銘柄の予測チャート表示

### MVP6: 銘柄プリセット (S&P500, 日経225, 債券, 金)
- supabase/migrations/002_asset_hierarchy.sql (asset_class, sub_class, industry, display_name)
- 60+銘柄の INSERT (米国株, 日本株, 債券ETF, 金ETF, REIT)
- Frontend: ツリー型銘柄セレクター (buildTree, チェックボックス, 検索)

### MVP7: 予算・購入日・重み付けの設定
- Frontend: 予算($)、Target Return Min/Max(%)、Max Weight(%) の数値入力
- 右サイドバー: 各銘柄のweight(%) 手動編集 + 合計100%バリデーション
- 購入日設定（全銘柄同時購入の仮定）

### MVP8: ポートフォリオ価値の推移を計算・表示
- ブラウザ内 MVO (calcMetrics, calcCov, opt 関数)
- 5戦略: max_sharpe, min_variance, target_return, max_return, equal_weight
- 時系列チャート: 実績=実線、予測=破線、ReferenceLine で境界
- 戦略カード + アロケーションバー + 銘柄テーブル

### MVP9: テスト
- Supabase Auth (profiles, user_id on portfolios, RLS)
- frontend/src/lib/auth.ts (signUp, signIn, signOut, onAuthStateChange)
- AuthModal (ログイン/新規登録)
- テスト項目: 株価表示、ログイン、ポートフォリオ検索/保存、予測確認、時系列表示

### MVP10: Mean-Variance Optimization (Optional)
- 投資予算 + 最低/最大リターン → 最適weight計算
- 複数戦略の比較表示
- Efficient Frontier の可視化

## 指示
1. 現在のコードベースを確認して、どのMVPまで完了しているか判断
2. 次のMVPの手順を詳しく説明
3. コード例を提示するが、実行はユーザー自身が行う（コマンド実行しない）
4. 完了チェックリストを提示
5. kadai5/ の既存実装を参考にする