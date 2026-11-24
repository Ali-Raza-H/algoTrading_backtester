# Execution Model

The authoritative source is one-minute OHLC data. Higher timeframes are aggregated
causally from complete one-minute intervals. A completed bar becomes visible at its
close; orders created from that callback cannot fill from the consumed bar.

## Bar Fills

| Order | Modeled behavior |
| --- | --- |
| Market | Next eligible bar open, executable side, plus adverse slippage |
| Limit | Touch of the executable range; favorable opening gaps improve price |
| Stop | Trigger on executable range; adverse gaps fill at the opening price |
| Stop-limit | Triggers first, then behaves as a limit from a later observation |
| Stop loss | Stop-market behavior on the executable liquidation side |
| Take profit | Limit-like behavior on the executable liquidation side |

If stop loss and take profit are both reachable in one bar, stop loss wins and the
trade is marked ambiguous. Candle data cannot reconstruct the hidden intrabar path.

The broker enforces sessions, price/quantity steps, expiry-before-matching, margin at
submission and fill, deterministic stop-out, commissions, and timezone-aware financing.
Hedging keeps independent positions. Netting reduces, closes, or reverses the current
position. Ledger entries reconcile to balance after every processed bar.

This release intentionally excludes quote-tick execution, depth, market impact,
capacity-based partial order fills, multi-currency conversion, and live brokerage.
Reports describe modeled results, not broker-quality fills or future performance.

