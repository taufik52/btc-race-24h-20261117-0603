# SPDX-License-Identifier: MIT
"""Range-trading bot for make.fun btc-race-24h-20261117-0603.

Trades cbBTC (CBBTC) against USDC, one-minute bars, long only, paper $1,000.
Speaks agent-fun/bars-replay/v1 directly: newline-delimited JSON on stdin
(hello, batch..., done) and exactly one reply per batch on stdout.

Strategy: buy the bottom of the range, sell the top of the range.
  - Keep the last 480 closes (8 hours). Let lo = min, hi = max of that window.
  - Enter (full cash) when the close sits in the bottom 15% of the window:
    close <= lo + 0.15 * (hi - lo). One position at a time, no pyramiding.
    The comparison is done in integer basis points of the range width, so
    no float ever decides an order.
  - Exit the whole position when any of these holds at a close:
      * the close makes a new 480-bar high (close >= hi), i.e. the range top;
      * the position is up 200 bps over its cost (take profit);
      * the position is down 150 bps under its cost (stop loss).
  - Orders decided on a bar fill at the next bar's open, so every signal is
    read off closes only; nothing depends on the fill price.

Why this shape: on 1-minute cbBTC the tape is choppy (typical 1-minute move
~6 bps) while a full-size round trip costs ~26 bps in fee, slippage and pool
impact, so scalping the noise loses. The money is in the multi-hour swings
(~1-2% peak to trough inside a day). Buying only near the rolling range
bottom and selling only at a new range high keeps the bot out of the chop
and lets one swing pay for the round trip several times over. The stop loss
caps the damage if a dip keeps dipping; the take profit banks a swing that
stalls just under the old high.

Deterministic: standard library only, no clock, no environment, no files,
no randomness. All arithmetic on the harness's integer units (prices at
scale 5, quantities at scale 8, USDC at scale 6) so the same bars always
give the same orders.
"""

import json
import sys

PROTOCOL = "agent-fun/bars-replay/v1"

# --- strategy constants (basis points / bars) -------------------------------
WINDOW_BARS = 480        # 8 hours of 1-minute closes
ENTRY_FRACTION_BPS = 1500  # enter in the bottom 15% of the window range
TAKE_PROFIT_BPS = 200    # exit when the position is up 200 bps on cost
STOP_LOSS_BPS = 150      # exit when the position is down 150 bps on cost
MAX_SLIPPAGE_BPS = 100   # per-order slippage cap vs the latest close
MIN_NOTIONAL = 1000000   # 1 USDC, in quote units (the harness minimum)

# --- unit scales (from the hello message; kept as fallbacks) ----------------
PRICE_SCALE = 5          # cbBTC price: 1 unit = 1e-5 USDC
QTY_SCALE = 8            # cbBTC quantity: 1 unit = 1e-8 BTC
QUOTE_SCALE = 6          # USDC: 1 unit = 1e-6 USDC


def position_value(qty, close):
    """What qty of the asset is worth at close, in quote units (truncated)."""
    return qty * close * 10 ** QUOTE_SCALE // 10 ** (QTY_SCALE + PRICE_SCALE)


class RangeBot:
    def __init__(self, hello):
        self.hello = hello
        self.symbol = hello["symbols"][0]["symbol"]
        self.closes = []          # closes of the symbol, oldest first
        self.cash = int(hello["capital"]["quote_units"])
        self.qty = 0
        self.cost = 0
        self.next_id = 1
        self.warmup_bars = hello.get("warmup_bars", 0)
        self.seq = -1

    # -- account -----------------------------------------------------------
    def apply_account(self, account):
        self.cash = int(account["cash_quote_units"])
        self.qty = 0
        self.cost = 0
        for pos in account["positions"]:
            if pos["symbol"] == self.symbol:
                self.qty = int(pos["qty"])
                self.cost = int(pos["cost_quote_units"])

    # -- decisions ---------------------------------------------------------
    def decide(self):
        """Orders for this batch, decided on the latest close. [] for none."""
        if self.seq < self.warmup_bars:
            return []  # warm-up bars: build history, orders would be rejected
        if not self.closes:
            return []
        close = self.closes[-1]

        if self.qty > 0:
            value = position_value(self.qty, close)
            if self.cost > 0:
                pnl_bps = (value - self.cost) * 10000 // self.cost
                if pnl_bps >= TAKE_PROFIT_BPS or pnl_bps <= -STOP_LOSS_BPS:
                    return [self._sell_all()]
            if len(self.closes) >= WINDOW_BARS:
                window = self.closes[-WINDOW_BARS:]
                if close >= max(window):
                    return [self._sell_all()]
            return []

        if len(self.closes) < WINDOW_BARS:
            return []
        window = self.closes[-WINDOW_BARS:]
        lo = min(window)
        hi = max(window)
        if hi <= lo:
            return []
        # bottom 15% of the range, in integer bps of the range width
        if (close - lo) * 10000 <= (hi - lo) * ENTRY_FRACTION_BPS:
            if self.cash >= MIN_NOTIONAL:
                return [self._buy_full()]
        return []

    def _buy_full(self):
        order = {
            "side": "buy",
            "client_id": "o%d" % self.next_id,
            "symbol": self.symbol,
            "notional_quote_units": str(self.cash),
            "max_slippage_bps": MAX_SLIPPAGE_BPS,
        }
        self.next_id += 1
        return order

    def _sell_all(self):
        order = {
            "side": "sell",
            "client_id": "o%d" % self.next_id,
            "symbol": self.symbol,
            "qty": str(self.qty),
            "max_slippage_bps": MAX_SLIPPAGE_BPS,
        }
        self.next_id += 1
        return order


def main():
    bot = None
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        kind = msg.get("type")
        if kind == "hello":
            if msg.get("protocol") != PROTOCOL:
                raise SystemExit("unsupported protocol: %r" % msg.get("protocol"))
            bot = RangeBot(msg)
        elif kind == "batch":
            bot.seq = msg["seq"]
            for bar in msg["bars"]:
                if bar["symbol"] == bot.symbol:
                    bot.closes.append(int(bar["c"]))
            bot.apply_account(msg["account"])
            orders = bot.decide()
            reply = {"type": "reply", "seq": msg["seq"], "orders": orders}
            out.write(json.dumps(reply, separators=(",", ":")) + "\n")
            out.flush()
        elif kind == "done":
            bot.apply_account(msg["account"])
            return 0
        else:
            raise SystemExit("unknown message type: %r" % kind)
    raise SystemExit("input ended before done")


if __name__ == "__main__":
    sys.exit(main())
