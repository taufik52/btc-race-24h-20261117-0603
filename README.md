# btc-race-24h-20261117-0603 — Range-Trading Bot (CBBTC/USDC)

Paper-trading bot for the make.fun "Race a BTC trading bot live for 24 hours"
competition. One file, Python 3.13 standard library only, deterministic.

## Strategy

Buy the bottom of the range, sell the top of the range:

- Keep the last **480 closes** (8 hours of 1-minute bars). Let `lo` = min,
  `hi` = max of that window.
- **Enter** with the full cash balance when the close sits in the **bottom
  15%** of the window: `close <= lo + 0.15 * (hi - lo)`. One position at a
  time, no pyramiding.
- **Exit** the whole position when any of these holds at a close:
  - the close makes a new 480-bar high (`close >= hi`) — the range top;
  - the position is up **200 bps** over its cost (take profit);
  - the position is down **150 bps** under its cost (stop loss).

Orders decided on a bar fill at the next bar's open, so every signal is read
off closes only.

## Why this shape

On 1-minute cbBTC the tape is choppy (typical 1-minute move ~6 bps) while a
full-size round trip costs ~26 bps in fee (4 bps), slippage (2 bps) and pool
impact, so scalping the noise loses. The money is in the multi-hour swings
(~1–2% peak to trough inside a day). Buying only near the rolling range
bottom and selling only at a new range high keeps the bot out of the chop
and lets one swing pay for the round trip several times over. The stop loss
caps the damage if a dip keeps dipping; the take profit banks a swing that
stalls just under the old high.

## Protocol

Speaks `agent-fun/bars-replay/v1` directly (no vendored SDK): newline-delimited
JSON on stdin (`hello`, `batch...`, `done`), exactly one reply per batch on
stdout. All arithmetic is on the harness's integer units (prices scale 5,
quantities scale 8, USDC scale 6); no floats decide an order, no clock, no
environment, no files, no randomness — the same bars always give the same
orders.

## Local grade (dev window)

`node <kit>/cli/makefun.mjs grade-local --kit <kit> --bot . --driver process`
could not run Python bots (process driver is Node-only); Docker was not
available in this VM, so the dev score below comes from a local protocol-level
replay that re-implements the wire and the exact fill math from the kit's
harness source (`harness/sim/bars/engine.ts`: buy price =
`open * (10000 + slippage) * (D + spend) / (10000 * D)`, sell price =
`open * (10000 - slippage) * D / (10000 * (D + value))`, fee 4 bps, all
divisions truncating). Two independent runs give identical results.

- **Net PnL: +$30.82** (dev gate needs > $0)
- **Max drawdown: -0.72%** (bar is ≤ 25%)
- **Trades: 3 round trips** (5 fills incl. final flatten), 0 rejected orders

Trades on the dev window: buy $63,974 → sell $65,093 (+$14.27); buy $64,132
→ sell $65,460 (+$17.82); buy $64,576 → flattened $64,776 (~$0).

Neighbouring parameters (window 420–480 bars, entry 15–20% of range) all score
+$23 to +$31 on the dev window, so the result is a plateau, not a single
lucky parameter set.
