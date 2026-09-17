# llm-trader

A market-context engine that feeds a formatted dashboard to a local LLM every 5 minutes and asks
one question: **enter, or do nothing?** If it enters, it must supply a stop and a target.

Inspired by the setup [glblackmamba](https://reddit.com/r/algotrading) described for MNQ futures,
rebuilt for US equity ETFs (SPY/QQQ) with a local model and a paper broker so it can be run and
judged for free.

```
 1m/5m/1h bars ──► context engine ──► dashboard text ──► LLM ──► decision JSON
                   (trend, momentum,         (~500 tokens)      (action, entry,
                    mean reversion,                                stop, target,
                    volatility, volume,                            confidence)
                    session, regime)                                  │
                                                                      ▼
                          journal ◄── trades ◄── paper broker ◄── risk gate
                        (JSONL)                (sim fills or        (confidence,
                                                Alpaca paper)       R:R, ATR band,
                                                                    daily loss cap)
```

## Quickstart

```bash
make setup          # venv + deps
make check          # python, ollama, model, data, keys
make test           # unit tests (indicators, context, risk, parser, broker, gate, news)
make once           # one decision cycle right now (no account needed)
```

Replay a past session (this is how you actually judge it):

```bash
make replay DATE=2026-09-16                 # 74 decision windows, gated, ~15 min on a local 7B
make backtest DAYS=5 GATE=0                 # every window, no gate: ~10x more LLM calls
make report                                 # win rate, expectancy, R multiples, per-regime
make study                                  # do the scored dimensions predict anything at all?
```

Run it forward on live data with simulated fills:

```bash
make paper          # loop every 5 min during market hours, no broker account needed
make stop
```

Run it forward against a real Alpaca **paper** account (free API keys in `.env`):

```bash
cp .env.example .env    # paste ALPACA_API_KEY / ALPACA_SECRET_KEY (paper, not live)
make live               # sleeps until the open, then a decision every 5 minutes
make status             # is it running, what has it done, what does it hold
make stop
```

For a run that survives you closing the laptop, wrap it:

```bash
nohup caffeinate -i python -u scripts/live.py --broker alpaca --gate > live.log 2>&1 &
```

`-u` matters: without it Python buffers stdout and `live.log` stays empty for hours. `caffeinate -i`
stops macOS idle sleep from pausing the loop mid-session.

## Why the pieces are shaped this way

**Market context, not raw bars.** The LLM gets a computed context: EMA stack and regression slope
per timeframe, R² for trend quality, ADX/+DI/-DI, RSI, stochastic, MACD histogram, Bollinger %B and
band width, ATR as a percent of price, z-score versus the 20-bar mean, swing structure, relative
volume, plus session context (gap, VWAP distance, position in the day's range, time-of-day volume
relative to the last 5 sessions) and key levels. Numbers, not narrative. `llmtrader/context/`.

**It sees only completed bars.** `build_context` drops any bar whose window has not closed yet, so
the 15:35 snapshot cannot contain 15:35's own close. This is the single most important line of
defence against a backtest that lies to you.

**The dashboard is a prompt, and it is bounded.** ~500 tokens, fixed-width tables, a column guide
so the model cannot misread a field, plus the account block and its own recent decisions so it can
see that the last three shorts failed. `llmtrader/dashboard.py`.

**The model must defend the trade.** Output is schema-constrained JSON, then validated twice:
structurally (parses, required fields) and semantically (stop on the losing side, target on the
winning side, entry within 0.15% of price, R:R ≥ 1.5, stop inside a 0.5–3× ATR(5m) band). A failed
check is fed back to the model for a retry with the specific objection. `llmtrader/trader.py`.

**Risk is deterministic, not a prompt.** Confidence floor, R:R floor, ATR-bounded stops, max trades
per day, max consecutive losses, a daily loss cap that halts trading, no entries in the first 5 or
last 10 minutes, one position at a time, and position size derived from stop distance
(0.25% of equity risked per trade, capped at 10% notional). The LLM proposes; `llmtrader/risk.py`
disposes.

**Two execution paths, one decision path.** `SimBroker` fills at the next bar's open with slippage
and walks stop/target/max-hold bar by bar, taking the stop first when a bar covers both (the
pessimistic choice). `AlpacaBroker` submits bracketed market orders to a paper account. Same
context, same prompt, same guards.

**A gate for cost control.** `llm_gate: true` wakes the model only when something looks actionable
(ADX, relative volume, band width, stretched z-score, swing structure, session extremes, VWAP
distance). Off by default so you can measure what the gate costs you in signals.

**Everything is journaled.** Every window writes its dashboard, raw model reply, parsed decision,
risk verdict and account state to `runs/<run>/`, so any trade can be traced back to the exact text
the model saw.

## What runs are logged

| file | contents |
|---|---|
| `runs/<run>/decisions.jsonl` | one row per 5-minute window: context regime, decision, risk verdict, account |
| `runs/<run>/trades.jsonl` | closed trades with entry, exit, reason, PnL, R multiple, MFE/MAE, hold time |
| `runs/<run>/prompts/*.prompt.txt` | the exact dashboard text sent |
| `runs/<run>/prompts/*.raw.txt` | the exact model reply |
| `runs/<run>/meta.json` | config snapshot and run parameters |

Exit reasons are tracked separately (`stop`, `take_profit`, `max_hold`, `session_end`) because the
distribution tells you more than the PnL does: a strategy that only ever exits on `max_hold` is not
a strategy.

## Swapping models

`llm_backend: ollama` (default, free) works with anything installed locally:

```bash
make backtest DAYS=3 MODEL=qwen2.5:7b
make backtest DAYS=3 MODEL=qwen3:4b
make backtest DAYS=3 MODEL=hermes3:8b
```

`deepseek` (direct API, ~$0.30/day at this volume) and `opencode-go` (your existing subscription)
are also wired up. Note that opencode Go is documented for coding-agent traffic and monitors for
abuse — a trading bot calling 288×/day is not that, so use the direct DeepSeek key if you want a
hosted model.

Reports are per-run, so `make report` gives you a side-by-side read on which model actually trades
better rather than which one sounds better.

## Adding the politician-tweet weigher

The hook already exists: `llmtrader/news.py` ships a dependency-free RSS/Atom reader. Point it at
any feed and recent headlines become `--- ADDITIONAL CONTEXT ---` lines inside the prompt, with
age in minutes:

```yaml
news_urls:
  - https://example.com/politics.rss
  - https://example.com/truth-social.rss
news_lookback_min: 180
news_max_items: 6
```

The model sees `HEADLINE 4m ago [source]: ...` and weighs it against the tape. Deliberately no
sentiment scoring in code — that is the LLM's job, and doing it twice hides the error. When you
want it stronger, the next steps are (1) a keyword pre-filter so only market-moving names/topics
reach the prompt, (2) a per-headline timestamp so the model can see whether the move already
happened, and (3) logging which headlines were present at each entry so you can measure whether
they helped.

## What the first three runs actually showed

Same session (SPY, 2026-09-16), same model (qwen2.5:7b), 74-76 five-minute windows. Run 1 is the
naive version; run 2 adds the counter-trend guard and the prompt rule that the thesis must support
the action; run 3 is the shipped configuration, with the gate requiring participation and the
cross-market data-loss bug fixed.

| | run 1 | run 2 | run 3 |
|---|---|---|---|
| decision windows | 74 | 76 | 76 |
| LLM calls | 57 | 19 | 14 |
| gated out | 4 | 51 | 62 |
| parse failures | 0 | 0 | 0 |
| entries proposed | 7 long / 0 short | 2 long / 1 short | 1 long / 3 short |
| entries taken | 4 | 1 | 4 |
| trades | 4 (1W/3L) | 1 (1W/0L) | 4 (1W/3L) |
| total R | -2.65 | +0.59 | +0.60 |
| max drawdown | $87 | $0 | $40 |
| avg latency | 9.3 s | 3.4 s | 1.6 s |

**Do not read that as a progression of improving edge.** Four trades is not a sample; run 3's +0.60R
is one +$54 winner carrying three small losers. The things that are actually informative:

1. **The gate needed to require participation, not just a trigger.** The first version asked "is
   ADX high, is volume up, is the band wide?" as an OR, and on a trending day that is always true —
   it filtered 4 windows out of 74. Requiring *both* a directional trigger and real participation
   (volume or displacement from VWAP or a session extreme) filtered 51 of 76, cut LLM calls by two
   thirds, and halved latency. `make backtest DAYS=5 GATE=0` versus `GATE=1` is the experiment that
   tells you what the gate costs in signals — nobody has run it yet.

2. **The model's action drifted from its own reasoning, and the deterministic guards caught it.**
   In run 1, three of the four trades were `enter_long` with theses that read "5m bearish setup",
   "EMAs bearish, ADX confirms trend down", "price near session low" — buying into a downtrend while
   describing it. In run 2 the same thing happened twice more (confidence 9, so the counter-trend
   floor let them through; both were rejected by the ATR stop-width band instead), and then two
   windows later the model finally issued the `enter_short` its own thesis had been describing all
   along — which is the trade that made money.

   That is the argument for keeping risk rules outside the model. A prompt instruction is a nudge,
   not a guarantee: 18:55 and 19:00 in run 2 prove the model will still write "downtrend" and pick
   `enter_long` with high confidence. If you want to close that hole properly, add a required
   `thesis_direction` field to the schema and reject any decision whose declared direction
   contradicts its action — cheap, deterministic, and it cannot be argued with.

3. **A data source can fail silently and you will not notice.** Reading the run-1 prompts back,
   `--- CROSS MARKET ---` was missing from most of them: the yfinance fetch for QQQ/IWM/VIXY was
   swallowed by a bare `except: continue` in `cross_market`, so the model was making decisions with
   a third of its context quietly absent and nothing in the logs said so. Fixed by recording the
   failure as a `DATA WARNING` line inside the prompt itself, caching for 15 minutes so one good
   fetch serves the session, and adding tests that assert a warning when a symbol is missing, empty,
   or returns a zero close. In run 3 all 14 LLM calls carried cross-market data and no warnings.
   Any feed this system trusts should be held to the same standard.

What the runs also show, cheaply: zero parse failures in 150 windows across both runs, which means
schema-constrained local inference plus the retry loop is reliable enough to build on.

## The Alpaca paper path, verified against the live API

Alpaca's paper endpoint is a real broker with real validation, and it rejected two things the sim
broker happily accepts. Both are now guarded:

- **Bracket legs are validated against the live price, not the price the model proposed.** With SPY
  at 754.05, a decision whose entry was 762 came back
  `422 stop_loss.stop_price must be <= base_price - 0.01`. The trade would have vanished with only a
  traceback in a log file. `bracket_problems()` now checks the legs against a freshly fetched live
  price before submitting and raises a `BrokerError` that the engine records in the journal as
  `entry_not_placed`, so a decision that could not be executed is visible rather than assumed.
- **A DAY order placed after the close fills at the *next* open**, at a price unrelated to the
  decision that produced its stop and target. `submit()` now refuses to place anything outside
  regular trading hours unless `allow_after_hours` is set, and the live loop cancels an unfilled
  entry when the session ends.

Still unverified: the fill → `sync()` → trade-recording path, because it needs a real fill and the
market was closed when this was built. That is the one thing to watch on the first live session —
`runs/<run>/events.jsonl` will show `exit_*` events if it is working.

## Against the reference architecture

This project was built from a Reddit description of the setup, not from the author's architecture
document. Reading that document afterwards, the honest diff is: I matched the outer shape and
missed the component that makes it work, and I broke two of its stated rules.

| Reference architecture | Here |
|---|---|
| Dashboard as formatted text, not JSON | same |
| Drop the in-progress bar, dedup by completed timestamp | same |
| Multi-timeframe bars (15 primary / 15 short / 12 long) | same |
| EMA 9/21/50, RSI 14, MACD 12/26/9, ATR 14, BB 20/2σ, VWAP, regime | same |
| Session-aware volume vs time-of-day bucket | same |
| **No post-decision AI review — brackets manage exits** | same |
| **7 continuous scored dimensions** | **missing until now** — see below |
| IQR outlier removal in the volume baseline | now added |
| Classic pivot points | now added |
| Structured trade feedback with pattern detection | now added |
| GEX overlay, omitted when stale | not built (optional in the reference too) |

### The missing scorer was the actual bug

The reference does not send raw indicator tables and hope the model synthesises a view. It
publishes continuous scores — Trend and Momentum on −100..+100 with labels, Mean Reversion 0..100
*with a snap direction*, Volatility and Volume as labels, plus S/R proximity in ATR units. The
model then spends its capacity deciding instead of recomputing.

Without those, my model had to invent the synthesis every tick, and it made mistakes that looked
like a reasoning failure but were really a missing input. Before the scorer, three of four trades
were `enter_long` carrying a thesis that read "5m bearish setup", "EMAs bearish, ADX confirms trend
down", "price near session low" — buying while describing a decline. I patched that with a
counter-trend confidence floor, which is the thing the reference explicitly argues against:

> *"Why no trend guard or hard gates? The AI already sees the trend score. A hard gate that blocks
> counter-trend trades also blocks valid mean reversion entries. Trust the AI's synthesis."*

They are right, and the fix was not the gate — it was the input. With the Mean Reversion dimension
and its snap direction in the prompt, the same session produces:

```
19:15  trend_down  enter_long   conf 9   "Trend is bearish but MeanRev is extremely stretched,
                                         suggesting a mean-reversion setup. The price is below
                                         the session VWAP and near pivot_S2."
19:20  trend_down  enter_short  conf 9   "Trend is strongly bearish with ADX at 35, EMA stack in
                                         bear order, and price rejecting VWAP from below."
```

Two decisions, both internally coherent, one of each direction, each naming the evidence. The
contradiction is gone, and neither needed a gate to produce. That is the difference between a
model that cannot see the mean-reversion case and a model that can.

### Gate A/B, same session, same model

`make backtest GATE=1` vs `GATE=0`, both with the scorer in place:

| | gate ON | gate OFF |
|---|---|---|
| LLM calls | 22 | 76 |
| gated out | 51 | 0 |
| in-position skips | 3 | 0 |
| non-hold decisions | 2 | 1 |
| trades | 1 | 0 |
| total R | −0.53 | 0.00 |
| avg latency | 4.1 s | 14.3 s |
| wall clock | 6.5 min | 19 min |

The gate saved 54 calls and two thirds of the wall clock, and on this day cost no signal — the
un-gated run proposed *fewer* trades (1 vs 2), and the one extra gated-in entry lost money. One
session proves nothing, but it does show the gate is not the signal-destroying filter the reference
warns about, at least not at these thresholds.

### The one rule I still knowingly break

**Balance-delta P&L.** The reference insists account balance is the only number that is always
correct, and that trade sums miss commissions, slippage and prior-session fills. It was right:
`day_pnl` now derives from equity minus the day's opening balance, so the daily loss halt fires on
real drawdown — including unrealized drawdown on an open position — rather than on my own
bookkeeping. Their own log shows why this matters: a single position's P&L was corrected from
−$773.92 to −$1,158.64 when the balance was consulted. A 50% miss on one trade is more than enough
to walk through a daily loss limit unnoticed.

What is *not* copied: I still keep `max_hold_minutes` as a time stop (the reference relies purely on
the SL/TP brackets), and my schema uses absolute prices rather than ticks, because equities. Both
are deliberate, and the time stop is worth watching — in the pre-scorer runs, half of all exits were
`max_hold` rather than target, which is a sign the targets are set beyond what the market delivers.

## What the signal study found, and what it changed

`make study` computes the same context and scores the bot sees at every decision window, with no
LLM involved, then measures what price did next. Two methodological decisions made the output
usable:

- **Excess returns.** These sessions had real drift, so in a falling market every short-biased rule
  "works" and every long-biased rule "fails" for no reason but beta. Every number is measured
  against the same session's unconditional mean, so drift is subtracted out. This mattered: one
  rule that looked like **+18.4 bps** on raw returns was **+9.8 bps** once drift was removed, and a
  further half of that evaporated in the out-of-sample half.
- **Sample size.** 1-minute bars only go back about a week. 5-minute bars go back 60 days, and the
  same question works at 5-minute resolution, so the study runs there: **3,940 windows over 60
  sessions** instead of 228 over 3.

The result inverts the architecture's central assumption:

| Trend label | n | 60m excess return | t | win rate |
|---|---|---|---|---|
| STRONG_BULL | 251 | **−6.68 bps** | −6.01 | 34% |
| BULL | 1,264 | **−3.83 bps** | −7.63 | 40% |
| NEUTRAL | 1,137 | +0.18 bps | +0.29 | 49% |
| BEAR | 1,060 | **+4.69 bps** | +7.39 | 60% |
| STRONG_BEAR | 228 | **+5.88 bps** | +4.61 | 63% |

Perfectly monotonic, and it survives a first-half/second-half split with the same sign in both
halves. On SPY at a 5-minute decision horizon, **the trend and momentum dimensions are contrarian
indicators.** Reading them at face value is a losing habit — my own system prompt told the model
"in trend regimes you want pullback continuation, not counter-trend fades," which the measurement
says is backwards. Momentum agrees (STRONG_UP → −5.97 bps, t = −4.31).

The largest measured effect is fading high-ADX, high-volume windows: **+9.39 bps**, t = 6.13,
n = 283, positive in both halves (+14.3 / +4.4). Following the trend in exactly those windows is
−11.4 bps. Note what that means for the gate: it wakes the model when ADX and participation are
high, which is precisely where the fade has measured value, so the gate happens to be pointed at
the right windows for the right reason.

### What changed as a result

1. `llmtrader/empirical_priors.json` is generated by the study and quoted in the prompt under
   **EMPIRICAL PRIORS**, including the label-resolution table, the rules that agreed across both
   halves, and the caveats. Regenerate with `python scripts/study.py --granularity 5m --write-priors`.
2. The system prompt no longer asserts that trend regimes should be traded for continuation. It
   now says the measurement wins over instinct where they disagree.
3. `--split` reports every key rule in both halves of the sample, so a rule that only works in one
   half is visible rather than flattering.

### What I am not claiming

The effect is **roughly 0.4 of a 5-minute ATR**, which is far smaller than the noise on any single
trade. Overlapping windows inflate these t-statistics by about a factor of three, so a t of 6 is
closer to a t of 2 once corrected. Sixty sessions of one market in one regime is not a cycle. And a
prior this small does not survive a discretionary process that adds slippage, mistimed entries and
an LLM's own variance — which is exactly why the risk rules and the cost floor exist in code.

What the study does establish is the direction of the lean, and that the architecture's natural
reading of its own dimensions was the wrong way round. That is worth more than any prompt
rewording, and it is the honest ceiling of what this data can tell us.

## Running it on a server

```bash
make economics EQUITY=1000     # what it could make, what it costs, how long to prove it
```

**Do not run the model on the server.** A GPU host that serves a 7B model at usable speed costs
$200-700/month; the same work through a cheap hosted API is about **$0.01-0.30/month** at 22 gated
calls a day. Run a $5-12/month VPS for the bot and call the API. Use `--broker alpaca` on a server
specifically because the brackets live on Alpaca's side and survive the process dying, which the
memory-resident sim broker does not. Full walkthrough including the systemd unit:
[docs/remote.md](docs/remote.md).

## Why I am not claiming a gain from the scanner

`scripts/scan.py` applies the measured reversal rule mechanically across a basket, with no LLM, and
the same risk rules. Its first run looked excellent: **+3.85% over 60 sessions, 294 trades, 48.3%
win rate**, positive in all four symbols. That result was not real, and the way it failed is worth
more than the result would have been.

Three checks, in order:

**1. Sanity controls.** Same mechanics, direction flipped and randomised. `make controls`.

| arm | trades | win rate | net | profit factor |
|---|---|---|---|---|
| real reversal | 265 | 48.7% | **+3.39%** | 1.48 |
| random direction | 301 | 49.8% | +3.06% | 1.37 |
| **flipped direction** | 315 | **54.9%** | **+6.06%** | **1.80** |

Every arm profited and the *anti-signal* profited most. That ordered the arms backwards from what
the study measured, which meant the simulator was handing out an advantage unrelated to direction.
Two real bugs, both in `SimBroker`:

- **Stop and target were anchored to the decision price, not the fill.** The decision references
  the last *completed* bar's close; the fill happens at the *next* bar's open. With a 2:1
  target:stop, any drift in the gap puts the entry nearer the target than the stop — for either
  direction. A bracket anchored at the fill is what a real broker does, and it is now what this
  does.
- **The fill bar was never managed.** `_manage` skipped `bar.ts <= pos.opened_at`, giving every
  position one free bar in which the stop could not fire. A bar's open is its first tick, so its
  high and low are necessarily *posterior* to a fill at that open. The fill bar is now managed, and
  both bugs have regression tests.

After the fix the arms ordered correctly: real **+3.85%** / 48.3% wins, random −0.09%, flipped
−0.81% / 31.5% wins. A 48.3% hit rate against the 33.3% a random walk gives at 2:1 target:stop is a
real directional signal.

**2. Resolution check.** The 5-minute result depends on 5-minute bars resolving a stop that is one
5-minute ATR away — the simulation is operating exactly at its own resolution limit. Re-run on
1-minute bars, where a 1-ATR stop resolves across several bars: **36 trades, 8.3% win rate, −0.45%**,
with 92% of exits on the stop. The two resolutions disagree qualitatively.

The cause is visible in the loop: at 5-minute granularity the stamps are five minutes apart, so
there is a **five-minute gap between the price the signal saw and the price it filled at**; at
1-minute granularity that gap is one minute. The 5-minute edge lives in that gap. I have not
isolated whether the gap flatters the result or the 1m sample is simply too small — it is 7 sessions
against 60 — but either way **the simulator is not resolution-stable, so its performance numbers
cannot be trusted yet.**

**3. Sample discipline.** Seven sessions of 1-minute data versus sixty of 5-minute. Neither is a
cycle, and both cover the same single bull-to-chop regime.

So: the scanner is committed, the bugs it exposed are fixed, and the harness now carries its own
falsification tests. No performance claim survives them. That is the correct state of the evidence,
and it is worth restating the pattern — three times now an apparent edge from this system has
evaporated or inverted under a control, a split, or a resolution change. The measured ~5 bps prior
from the study is the only number that has survived scrutiny, and it is far too small to trade
profitably at any account size this project can reach.

## The one thing that survived everything: the deterministic reversal rule

Every LLM-driven variant in this repo has failed its controls. The formula has not. After fixing
the fill model and running the falsification suite, the deterministic reversal scanner produces:

| check | result |
|---|---|
| 60 sessions, 5m, 4 symbols | **+4.09%** at the shipped 10% notional cap, PF 1.58 |
| controls (flipped / random) | flipped **−0.98%**, random +0.76% — correctly ordered and symmetric |
| walk-forward: priors from sessions 1-38, tested on 39-60 | **+2.46%**, 57.0% win rate, PF 2.47 |
| same signals, exits resolved on 1m bars instead of 5m | **62.9% win rate**, PF 2.85 |
| full notional deployment (100%, no leverage) | **+32.9%**, 52.2% win rate, PF 1.89, max DD 2.7% |

A 52% hit rate against the 33.3% a random walk gives at a 2:1 target:stop is the edge, and it is
directional: inverting it loses, randomising it goes flat. Three independent checks agree.

### The catch, and it is the whole catch

The result is extremely cost-sensitive, because a 1-ATR stop on a 5-minute bar means the average
trade is over in about three minutes and pays the spread on every round trip:

| round-trip cost | net over 60 sessions | profit factor |
|---|---|---|
| 3 bps | +32.9% | 1.89 |
| 6 bps | +27.4% | 1.71 |
| 10 bps | +16.8% | 1.41 |
| 16 bps | +4.6% | 1.10 |
| **24 bps** | **−12.6%** | 0.74 |

Break-even is around **16 bps round trip**. TQQQ trades one to three cents wide on a ~$70 price,
which is roughly 4-6 bps round trip, so the realistic expectation sits in the +27% row — but with
only a 2-3x margin to break-even, and with 78% of the trades in TQQQ, the widest-spread instrument
in the basket. Every basis point of worse execution costs about 2% of the result.

### What this means for the architecture

**Remove the LLM from the trade decision.** This is the conclusion the whole project has been
walking towards. The same idea expressed as a formula returns +27-33% over three months; expressed
as an LLM reading a dashboard it returned nothing, and its theses contradicted its own actions. The
LLM's comparative advantage is unstructured text, not arithmetic on RSI tables, and here the
arithmetic wins. Keep the model for news if you want it; take it out of the entry decision.

Second: **the shipped `max_notional_pct: 10` is silently starving the strategy.** With a 1-ATR stop,
the risk-based size wants about 147% of equity of notional, so the 10% cap binds and every trade runs
at 0.04% risk instead of the configured 0.25%. Raising it to 100% requires no leverage and reaches
the intended risk budget. That is a risk decision, not a free parameter, and it is why the returns
looked tiny for so long.

Third, the honest caveats. Strategy parameters (`min_score=60`, 1-ATR stop, 2-ATR target) were chosen
while looking at this data, so the strategy-level walk-forward is incomplete even though the priors
were properly held out. Sixty sessions of one regime is not a cycle. 78% of the profit comes from one
leveraged ETF. And none of this has been run against live fills, where the spread is the assumption
most likely to be wrong.

## Honest limits

- **A profitable-looking replay is not evidence of edge.** These runs cover single sessions with
  one local model. Treat every result as a hypothesis to test, not a result.
- yfinance gives 1m bars for only the **last 7 days**, and 5m for 60. That bounds backtests.
  It also rate-limits and returns empty frames under load, which is how the missing cross-market
  bug above got in. Treat free bar data as unreliable and assert on it.
- Free data is IEX (a slice of the tape) and delayed where yfinance is the source. Alpaca's free
  tier has the same IEX limitation.
- Fill assumptions are optimistic in one direction and pessimistic in another: next-bar-open fills
  with slippage are reasonable, but intrabar stop/target ordering is unknowable from 1m bars, so
  the sim always assumes the stop hit first.
- No order flow, no options/GEX, no Level 2, no handling of halts or gaps through the stop. The
  Reddit setup used a home-built GEX calculator from delayed options data; that is not here yet.
- The Alpaca paper path is verified for account reads, order submission, bracket-leg validation and
  cancellation. The fill → close → `sync()` → trade-journal path has not yet run against a real fill.
- A small measured edge (~5 bps at 60m) has to survive costs, slippage and mistimed entries. The
  study measures a lean, not a profit, and the empirical priors quoted in the prompt are one
  60-session sample of one instrument in one regime.
- The scored dimensions are hand-tuned composites, not fitted weights. They are interpretable on
  purpose, but they are my numbers, not the reference implementation's, and no one has validated
  that they rank setups in the right order.
- A model that reads "session VWAP" and "call wall" the same way you do is an assumption. Read the
  `thesis` field in `decisions.jsonl` and decide for yourself whether the reasoning is sound — that
  is the real output of this project.

## Layout

```
llmtrader/
  config.py        dataclass config, .env loader
  data/base.py     Bar, resampling, session/time helpers
  data/feeds.py    yfinance and Alpaca bar feeds with an optional frozen cache
  context/         indicators.py, engine.py (context, regime, key levels, floor pivots)
  scorer.py        7 continuous dimensions: trend, momentum, mean reversion + snap, vol, volume, S/R
  feedback.py      intra-session results: losing streaks, directional bias, repeated failures
  dashboard.py     context -> prompt text
  prompts.py       system prompt + decision JSON schema
  llm/             ollama / openai-compatible clients, mock client
  trader.py        parsing, semantic validation, retry loop
  risk.py          the deterministic gate and position sizing
  gate.py          when is the context worth waking the LLM for (the A/B above says it is cheap)
  news.py          RSS headlines as extra prompt context
  broker/          sim (bar-driven fills), alpaca paper, local accounting
  journal.py       JSONL run logging
  report.py        win rate, expectancy, R multiples
scripts/           check.py, replay.py, live.py, report.py
tests/             unit tests: indicators, context, risk, parsing, broker, gate, news
```
