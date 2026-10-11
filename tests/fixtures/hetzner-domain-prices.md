The JSON fixture was fetched on 2026-10-11 from:
https://www.hetzner.com/_resources/app/data/app/live_data_whois_EUR.json

Hetzner's public /whois/ page loads this feed. Its whois.js calls
`fetch("/_resources/app/data/app/live_data_whois_" + currency + ".json")`
and displays `(price + vatRate / 100 * price).toFixed(2)`. Prices are net EUR
per `period` years, not separate registration and renewal quotes. The CLI
shows the standard registration estimate for Germany (19% VAT) and explicitly
reports renewal as unknown. Actual premium-domain and customer-specific order
prices require the order form. This saved feed is a test fixture, not a runtime
price cache. Runtime reads the public feed and fails closed on format changes.
