from pathlib import Path
import re

import pandas as pd

from services.ticker_mapper import map_to_ticker


DEFAULT_PORTFOLIO_FILES = ["holdings.csv", "Stocks.xlsx", "stocks.xlsx"]


def _normalize_label(value):
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _find_first_match(columns, predicates):
    for predicate in predicates:
        for column in columns:
            if predicate(column):
                return column
    return None


def _first_non_empty(series):
    for value in series:
        if pd.notna(value) and str(value).strip():
            return value
    return None


def _prepare_broker_frame(df, ticker_col, qty_col, price_col, isin_col=None, source=None):
    selected_columns = [ticker_col]
    renamed_columns = ["stock_name"]

    if isin_col:
        selected_columns.append(isin_col)
        renamed_columns.append("isin")

    selected_columns.extend([qty_col, price_col])
    renamed_columns.extend(["quantity", "avg_price"])

    df = df[selected_columns].copy()
    df.columns = renamed_columns

    if "isin" not in df.columns:
        df["isin"] = None

    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")
    df["avg_price"] = pd.to_numeric(df["avg_price"], errors="coerce")
    df = df.dropna(subset=["stock_name", "quantity", "avg_price"])
    df = df[df["quantity"] > 0].copy()

    df["ticker"] = df.apply(
        lambda row: map_to_ticker(row["stock_name"], row.get("isin")),
        axis=1,
    )
    df["source"] = source

    unresolved = df[df["ticker"].isna()]
    if not unresolved.empty:
        print(f"⚠️ Unresolved holdings skipped from {source}:", unresolved["stock_name"].tolist())

    df = df.dropna(subset=["ticker"]).reset_index(drop=True)
    return df


def _load_holdings_csv(path):
    df = pd.read_csv(path)
    original_columns = list(df.columns)
    df.columns = [_normalize_label(col) for col in df.columns]

    ticker_col = _find_first_match(
        df.columns,
        [
            lambda col: col == "instrument",
            lambda col: col == "ticker",
            lambda col: col == "symbol",
            lambda col: "instrument" in col,
            lambda col: "ticker" in col,
            lambda col: "symbol" in col,
        ],
    )
    qty_col = _find_first_match(
        df.columns,
        [
            lambda col: col == "qty",
            lambda col: col == "quantity",
            lambda col: "qty" in col,
            lambda col: "quantity" in col,
        ],
    )
    price_col = _find_first_match(
        df.columns,
        [
            lambda col: col == "avg cost",
            lambda col: col == "average cost",
            lambda col: col == "avg price",
            lambda col: "avg" in col and "cost" in col,
            lambda col: "average" in col and "cost" in col,
            lambda col: "avg" in col and "price" in col,
        ],
    )

    print(f"✅ Detected CSV columns ({path.name}):", original_columns)
    print("DEBUG csv mapping:", ticker_col, qty_col, price_col)

    if not ticker_col or not qty_col or not price_col:
        raise Exception(f"❌ Required CSV columns not found in {path.name}")

    return _prepare_broker_frame(
        df,
        ticker_col=ticker_col,
        qty_col=qty_col,
        price_col=price_col,
        source=path.name,
    )


def _load_stocks_excel(path):
    df_raw = pd.read_excel(path, header=None)
    header_row = None

    for i in range(len(df_raw)):
        row = [_normalize_label(value) for value in df_raw.iloc[i].tolist()]

        if any("stock name" in value for value in row) and any("quantity" in value for value in row):
            header_row = i
            break

    if header_row is None:
        raise Exception(f"❌ Could not detect portfolio header row in {path.name}")

    df = pd.read_excel(path, header=header_row)
    original_columns = list(df.columns)
    df.columns = [_normalize_label(col) for col in df.columns]

    stock_col = _find_first_match(
        df.columns,
        [
            lambda col: col == "stock name",
            lambda col: col == "security name",
            lambda col: "stock" in col and "name" in col,
            lambda col: "security" in col and "name" in col,
            lambda col: "instrument" in col,
            lambda col: "name" in col,
        ],
    )
    isin_col = _find_first_match(
        df.columns,
        [
            lambda col: col == "isin",
            lambda col: "isin" in col,
        ],
    )
    qty_col = _find_first_match(
        df.columns,
        [
            lambda col: col == "quantity",
            lambda col: "quantity" in col,
            lambda col: "qty" in col,
        ],
    )
    price_col = _find_first_match(
        df.columns,
        [
            lambda col: col == "average buy price",
            lambda col: col == "avg buy price",
            lambda col: col == "average price",
            lambda col: "average" in col and "price" in col,
            lambda col: "avg" in col and "price" in col,
            lambda col: "buy" in col and "price" in col,
        ],
    )

    print(f"✅ Detected Excel columns ({path.name}):", original_columns)
    print("DEBUG excel mapping:", stock_col, isin_col, qty_col, price_col)

    if not stock_col or not qty_col or not price_col:
        raise Exception(f"❌ Required Excel columns not found in {path.name}")

    return _prepare_broker_frame(
        df,
        ticker_col=stock_col,
        qty_col=qty_col,
        price_col=price_col,
        isin_col=isin_col,
        source=path.name,
    )


def _resolve_input_paths(paths=None):
    if paths is None:
        candidates = DEFAULT_PORTFOLIO_FILES
    elif isinstance(paths, (str, Path)):
        candidates = [paths]
    else:
        candidates = list(paths)

    resolved = []
    seen = set()

    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            real_path = path.resolve()
            dedupe_key = str(real_path).lower()
            if dedupe_key not in seen:
                seen.add(dedupe_key)
                resolved.append(real_path)

    if not resolved:
        raise FileNotFoundError(
            f"❌ None of the portfolio files were found: {', '.join(str(candidate) for candidate in candidates)}"
        )

    return resolved


def _aggregate_portfolio(df):
    if df.empty:
        return df

    df = df.copy()
    df["invested_amount"] = df["quantity"] * df["avg_price"]

    grouped = (
        df.groupby("ticker", as_index=False)
        .agg(
            stock_name=("stock_name", _first_non_empty),
            isin=("isin", _first_non_empty),
            quantity=("quantity", "sum"),
            invested_amount=("invested_amount", "sum"),
            source=("source", lambda values: ", ".join(sorted({str(v) for v in values if str(v).strip()}))),
        )
    )

    grouped = grouped[grouped["quantity"] > 0].copy()
    grouped["avg_price"] = grouped["invested_amount"] / grouped["quantity"]

    return grouped[["ticker", "stock_name", "isin", "quantity", "avg_price", "source"]]


def load_portfolio(paths=None):
    resolved_paths = _resolve_input_paths(paths)
    frames = []

    for path in resolved_paths:
        suffix = path.suffix.lower()

        if suffix == ".csv":
            frames.append(_load_holdings_csv(path))
        elif suffix in {".xlsx", ".xls"}:
            frames.append(_load_stocks_excel(path))
        else:
            print(f"⚠️ Unsupported portfolio file skipped: {path.name}")

    if not frames:
        raise Exception("❌ No supported portfolio files were loaded")

    combined = pd.concat(frames, ignore_index=True)
    combined = _aggregate_portfolio(combined)

    print("✅ Portfolio rows loaded:", len(combined))
    return combined
