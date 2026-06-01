def calculate_metrics(row, current_price):
    quantity = row['quantity']
    avg_price = row['avg_price']

    invested = quantity * avg_price
    current_value = quantity * current_price
    profit = current_value - invested
    profit_pct = (profit / invested) * 100

    return invested, current_value, profit, profit_pct
