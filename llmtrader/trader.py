import json
import re
from dataclasses import dataclass

from .dashboard import render_dashboard
from .llm.base import LLMError
from .prompts import DECISION_SCHEMA, SYSTEM_PROMPT, build_user_prompt

ACTION_ALIASES = {
    "long": "enter_long",
    "buy": "enter_long",
    "enter_long": "enter_long",
    "short": "enter_short",
    "sell": "enter_short",
    "enter_short": "enter_short",
    "hold": "hold",
    "flat": "hold",
    "no_trade": "hold",
    "none": "hold",
    "wait": "hold",
}


@dataclass
class Decision:
    action: str
    confidence: int = 1
    entry: float = None
    stop_loss: float = None
    take_profit: float = None
    size_multiplier: float = 1.0
    max_hold_minutes: int = 60
    thesis: str = ""
    invalidation: str = ""
    model: str = ""
    latency_ms: int = 0
    prompt_tokens: int = None
    completion_tokens: int = None
    parse_ok: bool = True
    semantic_ok: bool = True
    parse_error: str = ""
    raw_text: str = ""
    attempts: int = 1

    @property
    def is_entry(self):
        return self.action in ("enter_long", "enter_short")

    @property
    def side(self):
        return "long" if self.action == "enter_long" else "short"

    def reward_risk(self):
        if not self.is_entry or None in (self.entry, self.stop_loss, self.take_profit):
            return None
        risk = abs(self.entry - self.stop_loss)
        if risk == 0:
            return None
        return abs(self.take_profit - self.entry) / risk

    def to_dict(self):
        return dict(self.__dict__)


def _coerce_float(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except ValueError:
        return None


def _coerce_int(v, default=None):
    f = _coerce_float(v)
    return default if f is None else round(f)


def extract_json(text):
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    while start != -1 and end > start:
        chunk = text[start : end + 1]
        try:
            return json.loads(chunk)
        except json.JSONDecodeError:
            end = text.rfind("}", 0, end)
    raise ValueError("no parseable JSON object found")


def parse_decision(text, model="", latency_ms=0):
    d = Decision(action="hold", raw_text=text, model=model, latency_ms=latency_ms)
    try:
        data = extract_json(text)
    except ValueError as e:
        d.parse_ok = False
        d.parse_error = str(e)
        return d
    if not isinstance(data, dict):
        d.parse_ok = False
        d.parse_error = "top level JSON is not an object"
        return d
    raw_action = str(data.get("action", "hold")).strip().lower().replace(" ", "_")
    d.action = ACTION_ALIASES.get(raw_action)
    if d.action is None:
        d.parse_ok = False
        d.parse_error = f"unrecognised action '{data.get('action')}'"
        d.action = "hold"
    conf = _coerce_int(data.get("confidence"), 1)
    d.confidence = max(1, min(10, conf if conf is not None else 1))
    d.entry = _coerce_float(data.get("entry"))
    d.stop_loss = _coerce_float(data.get("stop_loss"))
    d.take_profit = _coerce_float(data.get("take_profit"))
    mult = _coerce_float(data.get("size_multiplier"))
    d.size_multiplier = min(2.0, max(0.25, mult if mult is not None else 1.0))
    hold = _coerce_int(data.get("max_hold_minutes"), 60)
    d.max_hold_minutes = max(5, min(240, hold if hold is not None else 60))
    d.thesis = str(data.get("thesis") or "")[:600]
    d.invalidation = str(data.get("invalidation") or "")[:400]
    if d.action == "hold":
        d.entry = d.stop_loss = d.take_profit = None
    return d


class Trader:
    def __init__(self, client, cfg, logger=None):
        self.client = client
        self.cfg = cfg
        self.logger = logger

    def build_prompt(self, ctx, account=None, recent=None, extra=None):
        dashboard = render_dashboard(ctx, account=account, recent=recent, cfg=self.cfg)
        user = build_user_prompt(
            dashboard,
            min_confidence=self.cfg.min_confidence,
            min_reward_risk=self.cfg.min_reward_risk,
            extra=extra,
        )
        return dashboard, user

    def semantic_problems(self, decision):
        problems = []
        if not decision.is_entry:
            return problems
        if decision.entry is None:
            problems.append("entry is null but the action is an entry")
        if decision.stop_loss is None:
            problems.append("stop_loss is null but every entry requires a stop")
        if decision.take_profit is None:
            problems.append("take_profit is null but every entry requires a target")
        if problems or decision.entry is None or decision.stop_loss is None:
            return problems
        direction = 1.0 if decision.action == "enter_long" else -1.0
        if (decision.stop_loss - decision.entry) * direction >= 0:
            problems.append(
                f"stop_loss {decision.stop_loss} is on the wrong side of entry "
                f"{decision.entry} for a {decision.side}"
            )
        if (
            decision.take_profit is not None
            and (decision.take_profit - decision.entry) * direction <= 0
        ):
            problems.append(
                f"take_profit {decision.take_profit} is on the wrong side of entry "
                f"{decision.entry} for a {decision.side}"
            )
        rr = decision.reward_risk()
        if rr is not None and rr < self.cfg.min_reward_risk:
            problems.append(
                f"reward:risk is {rr:.2f} which is below the required {self.cfg.min_reward_risk}"
            )
        return problems

    def decide(self, ctx, account=None, recent=None, extra=None):
        dashboard, user = self.build_prompt(ctx, account, recent, extra)
        last_err = None
        last_kind = "parse"
        for attempt in range(1, self.cfg.llm_retries + 2):
            try:
                resp = self.client.complete(SYSTEM_PROMPT, user, json_schema=DECISION_SCHEMA)
            except LLMError as e:
                last_err = e
                last_kind = "transport"
                continue
            decision = parse_decision(resp.text, model=resp.model, latency_ms=resp.latency_ms)
            decision.prompt_tokens = resp.prompt_tokens
            decision.completion_tokens = resp.completion_tokens
            decision.attempts = attempt
            if decision.parse_ok:
                problems = self.semantic_problems(decision)
                if not problems:
                    return decision, dashboard
                last_err = "; ".join(problems)
                last_kind = "semantic"
                user = (
                    user
                    + "\n\nYour previous reply was rejected for these reasons:\n"
                    + "".join(f"- {p}\n" for p in problems)
                    + "\nHere is the reply you gave:\n"
                    + (resp.text or "")[:400]
                    + "\n\nReply again with ONLY the corrected JSON object. "
                    "Remember: for an entry, entry, stop_loss and take_profit are all required "
                    "numbers, the stop must sit on the losing side of entry, the target on the "
                    "winning side, and |take_profit - entry| / |entry - stop_loss| must be at "
                    f"least {self.cfg.min_reward_risk}."
                )
                continue
            last_err = decision.parse_error
            last_kind = "parse"
            user = (
                user
                + "\n\nYour previous reply was not valid JSON and was rejected.\n"
                + f"Error: {decision.parse_error}\n"
                + "Reply again with ONLY the JSON object, no prose, no code fences.\n"
                + "Here is what you sent:\n"
                + (resp.text or "")[:400]
            )
        return (
            Decision(
                action="hold",
                raw_text="",
                model=getattr(self.client, "model", ""),
                parse_ok=last_kind == "semantic",
                semantic_ok=last_kind != "semantic",
                parse_error=str(last_err)[:300],
                attempts=self.cfg.llm_retries + 1,
            ),
            dashboard,
        )
