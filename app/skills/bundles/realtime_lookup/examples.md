# Realtime Lookup Examples

## Weather Queries

**User**: "What's the weather in Melbourne today?"
**Intent**: weather_lookup
**Response**: "Melbourne today: Partly cloudy, 18°C - 24°C. Wind 15 km/h. 20% rain chance."

**User**: "Will it rain tomorrow in Sydney?"
**Intent**: weather_lookup
**Response**: "Sydney tomorrow: Cloudy with 60% rain chance. Bring an umbrella."

## Exchange Rate Queries

**User**: "USD to CNY exchange rate"
**Intent**: exchange_rate_lookup
**Response**: "1 USD = 7.24 CNY (as of 2026-03-19)"

**User**: "How much is 100 EUR in GBP?"
**Intent**: exchange_rate_lookup
**Response**: "100 EUR = 86.50 GBP"

## Stock Quote Queries

**User**: "What's the AAPL stock price?"
**Intent**: stock_quote_lookup
**Response**: "AAPL: $195.42 (+1.2% today)"

**User**: "Tesla stock"
**Intent**: stock_quote_lookup
**Response**: "TSLA: $242.15 (-0.8% today)"

## Crypto Quote Queries

**User**: "Bitcoin price"
**Intent**: crypto_quote_lookup
**Response**: "BTC: $42,350 USD (+2.1% 24h)"

**User**: "Ethereum in USD"
**Intent**: crypto_quote_lookup
**Response**: "ETH: $2,245 USD (-1.3% 24h)"

## Sports Score Queries

**User**: "Lakers vs Celtics final score"
**Intent**: sports_score_lookup
**Response**: "Lakers 112, Celtics 108 (Final)"

**User**: "Premier League standings"
**Intent**: sports_score_lookup
**Response**: "Manchester City leads with 82 points"

## Fuel Price Queries

**User**: "Petrol price in Sydney"
**Intent**: fuel_price_lookup
**Response**: "Sydney petrol: $1.89/L (as of today)"

**User**: "Diesel price Melbourne"
**Intent**: fuel_price_lookup
**Response**: "Melbourne diesel: $1.72/L"

## Clarification Examples

**User**: "What's the weather?"
**Intent**: weather_lookup (missing location)
**Response**: "I detected a weather query but need your location. Which city? E.g., 'Melbourne weather'"

**User**: "Stock price"
**Intent**: stock_quote_lookup (missing ticker)
**Response**: "I need the stock ticker. E.g., 'AAPL stock price' or 'Tesla stock'"

## Fallback Examples

**User**: "Bitcoin price in 2025"
**Intent**: crypto_quote_lookup (historical, not real-time)
**Response**: "I can get current Bitcoin prices. For historical data, I'd need to search. Would you like current price instead?"

**User**: "Obscure cryptocurrency price"
**Intent**: crypto_quote_lookup (not in real-time sources)
**Response**: "I couldn't find real-time data for that. Let me search the web for you..."
