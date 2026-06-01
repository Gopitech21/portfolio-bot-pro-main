def get_recommendations(market_data):
    long_term = []
    swing = []

    for stock in market_data:
        if stock['score'] >= 2:
            long_term.append(stock['ticker'])

        if stock['change'] > 2:
            swing.append(stock['ticker'])

    return long_term[:5], swing[:5]
