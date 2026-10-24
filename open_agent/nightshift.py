"""Meterless Nightshift as the primary model provider: NVIDIA Nemotron on this Mac, $0, with a signed receipt per call.

Nightshift's engine serves an OpenAI-compatible API on 127.0.0.1:8088/v1 (scripts/nightshift_headless.sh starts it
without the app window). Which agent uses which Nemotron model:

    agent         Nightshift app id    lane                 model id (registry)                        tools
    investigator  wlwt-investigator    nemotron-lightning   local-runtime/nemotron-3.5-lightning-30b-a3b  native
    verifier      wlwt-verifier        nemotron-nano        local-runtime/nemotron-3-nano-30b-a3b         none (JSON in text)

Why per-agent app ids: in Nightshift 1.2.1 an explicit model id is recorded as requested_model but does not pin
routing, because every app is bound to a mode on first contact (engine openai.mjs `forceModel: bind.bound ? null`,
proxy.js bindAppMode). The control socket's per-app lane allowlist does pin it: each agent's app id may use exactly
one lane. Every response is checked: routed_model must be the agent's model and the receipt must show either
`pinned-model:<id>` or the allowlist with that single lane. Anything else raises RoutingMismatch (never a silent
fallback to another model).

Env: NIGHTSHIFT_BASE_URL (default http://127.0.0.1:8088/v1), NIGHTSHIFT_HOME (default ~/nightshift-openagent/headless),
NIGHTSHIFT_TOKEN_FILE (default $NIGHTSHIFT_HOME/state/proxy-token, read on every request, never logged),
NIGHTSHIFT_<AGENT>_LANE to move an agent to the other lane (e.g. NIGHTSHIFT_VERIFIER_LANE=nemotron-lightning when the
Nano lane cannot be admitted), NIGHTSHIFT_TRACE (jsonl file that gets one receipt line per call).

    python -m open_agent.nightshift smoke [--out logs/nightshift-smoke-<ts>.json]
    python -m open_agent.nightshift export --trace <NIGHTSHIFT_TRACE file> [--scope <prefix>] --out logs/receipts-<run>.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace

try:  # the honest_memory agent treats an OpenAIError as "model unavailable, use the router"
    from openai import OpenAIError as _Unavailable
except ImportError:  # pragma: no cover - the agent extra is not installed
    _Unavailable = RuntimeError

REPO = Path(__file__).resolve().parent.parent
LIGHTNING = "local-runtime/nemotron-3.5-lightning-30b-a3b"
NANO = "local-runtime/nemotron-3-nano-30b-a3b"
LANES = {"nemotron-lightning": LIGHTNING, "nemotron-nano": NANO}
TIMEOUT_S = 180.0      # a cold lane load can take minutes; a warm answer takes seconds
RETRIES = 1
RETRY_DELAY_S = 5.0
# A lane the daemon has just reported ready reaches the engine a few seconds later; until then the engine answers
# 400 no_provider_configured or 503 local_runtime_not_ready. That wait is not a failed call: up to this long.
WARMING_WAIT_S = 45.0
WARMING_CODES = ("no_provider_configured", "local_runtime_not_ready")


@dataclass(frozen=True)
class Route:
    agent: str
    app: str
    lane: str
    model: str


DEFAULT_LANES = {"investigator": "nemotron-lightning", "verifier": "nemotron-nano"}


def route_for(agent: str) -> Route:
    lane = os.environ.get(f"NIGHTSHIFT_{agent.upper()}_LANE") or DEFAULT_LANES.get(agent)
    if lane not in LANES:
        raise ValueError(f"no Nightshift lane for agent {agent!r} (lane {lane!r}; known: {', '.join(LANES)})")
    return Route(agent, f"wlwt-{agent}", lane, LANES[lane])


def base_url() -> str:
    return os.environ.get("NIGHTSHIFT_BASE_URL", "http://127.0.0.1:8088/v1").rstrip("/")


def home() -> Path:
    return Path(os.environ.get("NIGHTSHIFT_HOME") or "~/nightshift-openagent/headless").expanduser()


def token_file() -> Path:
    return Path(os.environ.get("NIGHTSHIFT_TOKEN_FILE") or home() / "state" / "proxy-token")


def _token() -> str:
    try:
        tok = token_file().read_text().strip()
    except OSError as exc:
        raise NightshiftUnavailable(f"cannot read the Nightshift token file {token_file()}: {exc.strerror}") from exc
    if not tok:
        raise NightshiftUnavailable(f"the Nightshift token file {token_file()} is empty")
    return tok


class NightshiftUnavailable(_Unavailable):
    """Nightshift did not answer (not running, refused, timed out, lane not admitted)."""


class RoutingMismatch(RuntimeError):
    """Nightshift answered, but not with the agent's Nemotron model or not under its pin. Never swallowed."""


# --- receipts ---------------------------------------------------------------------------------

@dataclass
class Receipt:
    agent: str
    app: str
    scope: str
    receipt_id: str | None
    decision_id: str | None
    requested_model: str | None
    routed_model: str | None
    provider_model: str | None
    source_policy: str | None
    lanes: list[str] | None
    mode: str | None
    from_cache: bool | None
    cache_status: str | None
    cost_usd: float | None
    naive_usd: float | None
    privacy: str | None
    tool_degraded: object
    usage: dict = field(default_factory=dict)
    latency_ms: float = 0.0          # measured here, request to parsed response
    signed: bool = False             # the response carried the daemon's signed envelope

    def to_dict(self) -> dict:
        return asdict(self)


def extract_receipt(meterless: dict, route: Route, scope: str, latency_ms: float) -> Receipt:
    trace = meterless.get("trace") or {}
    body = meterless.get("receipt") or {}
    phases = trace.get("phases") or body.get("phases") or []
    policy = body.get("app_policy") or {}
    cost = meterless.get("cost") or {}
    cache = meterless.get("cache") or {}
    return Receipt(
        agent=route.agent, app=route.app, scope=scope, receipt_id=meterless.get("receipt_id"),
        decision_id=meterless.get("decision_id"), requested_model=meterless.get("requested_model"),
        routed_model=meterless.get("routed_model"), provider_model=meterless.get("provider_model"),
        source_policy=(phases[0].get("source_policy") if phases else None), lanes=policy.get("lanes"),
        mode=meterless.get("mode"), from_cache=meterless.get("from_cache"), cache_status=cache.get("status"),
        cost_usd=cost.get("actual_usd"), naive_usd=cost.get("naive_usd"),
        privacy=(meterless.get("privacy") or {}).get("path"), tool_degraded=body.get("tool_degraded"),
        usage=meterless.get("usage") or {}, latency_ms=round(latency_ms, 1),
        signed=bool(meterless.get("receipt_envelope")))


def check_routing(rcpt: Receipt, route: Route) -> None:
    """Fail loudly unless the call ran on the agent's Nemotron model under a pin Nightshift reports."""
    problems = []
    if rcpt.routed_model != route.model:
        problems.append(f"routed_model {rcpt.routed_model!r} != {route.model!r}")
    pinned = rcpt.source_policy == f"pinned-model:{route.model}"
    allowlisted = rcpt.source_policy == "local-only" and rcpt.lanes == [route.lane]
    if not (pinned or allowlisted):
        problems.append(f"source_policy {rcpt.source_policy!r} with lanes {rcpt.lanes!r} is neither "
                        f"'pinned-model:{route.model}' nor 'local-only' with lanes ['{route.lane}']")
    if rcpt.cost_usd not in (0, 0.0, None):
        problems.append(f"cost {rcpt.cost_usd} USD (a local lane costs 0)")
    if problems:
        raise RoutingMismatch(f"Nightshift receipt {rcpt.receipt_id} for {route.agent}: " + "; ".join(problems))


# --- the call ---------------------------------------------------------------------------------

def _post(path: str, payload: dict, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(base_url() + path, data=json.dumps(payload).encode(), method="POST",
                                 headers={"content-type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _get(path: str, headers: dict, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(base_url() + path, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _headers(route: Route, scope: str) -> dict:
    return {"Authorization": f"Bearer {_token()}", "meterless-app": route.app, "meterless-scope": scope,
            "meterless-trace": "on"}


@dataclass
class Reply:
    message: dict
    finish_reason: str | None
    receipt: Receipt
    meterless: dict              # the whole block as Nightshift returned it (no credentials in it)


def chat(messages: list[dict], *, agent: str, scope: str, tools: list | None = None, tool_choice=None,
         temperature: float | None = 0, max_tokens: int | None = None, timeout: float = TIMEOUT_S,
         retries: int = RETRIES, extra: dict | None = None, check: bool = True) -> Reply:
    """One chat completion through Nightshift for `agent`, with its pinned Nemotron model and receipt."""
    route = route_for(agent)
    body = {"model": route.model, "messages": messages, **(extra or {})}
    if temperature is not None:
        body["temperature"] = temperature
    if max_tokens:
        body["max_tokens"] = max_tokens
    if tools:
        body["tools"] = tools
        body["tool_choice"] = tool_choice or "auto"
    last: Exception | None = None
    attempt, warming_until = 0, time.monotonic() + WARMING_WAIT_S
    while True:
        t0 = time.perf_counter()
        warming = False
        try:
            data = _post("/chat/completions", body, _headers(route, scope), timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            last = NightshiftUnavailable(f"HTTP {exc.code} from Nightshift: {detail}")
            warming = any(code in detail for code in WARMING_CODES)
            if not warming and exc.code < 500 and exc.code != 429:   # a refusal is final
                raise last from exc
            if "empty_response" in detail:   # the model answered with nothing; asking again costs the same again
                raise last from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = NightshiftUnavailable(f"Nightshift unreachable at {base_url()}: {exc}")
        else:
            ms = (time.perf_counter() - t0) * 1000
            meterless = data.get("meterless") or {}
            rcpt = extract_receipt(meterless, route, scope, ms)
            if check:
                check_routing(rcpt, route)
            _trace(rcpt)
            choice = (data.get("choices") or [{}])[0]
            return Reply(choice.get("message") or {}, choice.get("finish_reason"), rcpt, meterless)
        if warming and time.monotonic() < warming_until:
            time.sleep(RETRY_DELAY_S)          # the lane is coming up: wait, without spending the retry
            continue
        if attempt >= retries:
            break
        attempt += 1
        time.sleep(RETRY_DELAY_S)
    raise last  # type: ignore[misc]


# --- one lane at a time (NIGHTSHIFT_SWAP=1) ---------------------------------------------------
# On a Mac that cannot hold both Nemotron lanes next to other work, the agent's lane is started before its call and
# the other lane is stopped, over the daemon's control socket (the same endpoints scripts/nightshift_headless.sh uses).
# Measured 2026-10-24: with both lanes loaded and the Mac swapping, Nightshift answered wlwt-verifier from the
# Lightning lane although its allowlist names only nemotron-nano (receipt rcpt_req_bm9j53u7; check_routing stopped
# the run). With one lane loaded no other Nemotron model is there to answer.

def swap_enabled() -> bool:
    return os.environ.get("NIGHTSHIFT_SWAP", "").strip().lower() in ("1", "true", "yes", "on")


def control_socket() -> Path:
    return Path(os.environ.get("NIGHTSHIFT_CONTROL_SOCKET") or home() / "run" / "runtime.sock")


def _ctl(method: str, path: str, timeout: float = 60.0) -> dict:
    import http.client
    import socket

    class _Unix(http.client.HTTPConnection):
        def connect(self):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect(str(control_socket()))
            self.sock = s

    conn = _Unix("meterlessd", timeout=timeout)
    try:
        conn.request(method, path)
        raw = conn.getresponse().read()
    except OSError as exc:
        raise NightshiftUnavailable(f"Nightshift control socket {control_socket()}: {exc}") from exc
    finally:
        conn.close()
    return json.loads(raw or b"{}")


def lane_states() -> dict[str, str]:
    return {l["id"]: l["state"] for l in _ctl("GET", "/admin/status").get("lanes", [])}


def ensure_lane(lane: str, wait_s: float = 300.0) -> dict:
    """Make `lane` the one loaded Nemotron lane: stop the others of ours, start it, wait until ready."""
    t0 = time.perf_counter()
    states = lane_states()
    if states.get(lane) == "ready" and all(s == "stopped" for l, s in states.items() if l != lane and l in LANES):
        return {"lane": lane, "swapped": False}
    stopped = [l for l, s in states.items() if l != lane and l in LANES and s != "stopped"]
    for other in stopped:
        _ctl("POST", f"/admin/lanes/{other}/stop")
    if states.get(lane) != "ready":
        _ctl("POST", f"/admin/lanes/{lane}/start")
    deadline = time.monotonic() + wait_s
    state = states.get(lane)
    while time.monotonic() < deadline:
        state = lane_states().get(lane)
        if state == "ready":
            return {"lane": lane, "swapped": True, "stopped": stopped, "seconds": round(time.perf_counter() - t0, 2)}
        if state in ("failed", "crashed"):
            break
        time.sleep(1.0)
    raise NightshiftUnavailable(f"lane {lane} did not become ready (state {state!r}) after "
                                f"{time.perf_counter() - t0:.0f} s")


RECEIPTS: list[Receipt] = []      # every receipt of this process, newest last


def _trace(rcpt: Receipt) -> None:
    RECEIPTS.append(rcpt)
    path = os.environ.get("NIGHTSHIFT_TRACE")
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as fh:
            fh.write(json.dumps(rcpt.to_dict()) + "\n")


def new_scope(prefix: str = "wlwt") -> str:
    """A fresh meterless-scope per run, so Nightshift's cache and answer memory do not leak between runs."""
    return f"{prefix}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:6]}"


# --- the OpenAI-shaped client the honest_memory agent uses -------------------------------------

def _ns(obj):
    if isinstance(obj, dict):
        return SimpleNamespace(**{k: _ns(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return [_ns(v) for v in obj]
    return obj


class _Completions:
    def __init__(self, owner: "OpenAIShaped"):
        self.owner = owner

    def create(self, *, model: str, messages: list, tools=None, tool_choice=None, temperature=None,
               reasoning_effort=None, **kwargs):
        o = self.owner
        route = route_for(o.agent)
        if model != route.model:
            raise RoutingMismatch(f"{o.agent} asked for {model!r}; its Nightshift model is {route.model!r}")
        extra = {k: v for k, v in kwargs.items() if k in ("max_tokens", "response_format", "stop", "seed")}
        if reasoning_effort:
            extra["reasoning_effort"] = reasoning_effort
        reply = chat(messages, agent=o.agent, scope=o.scope, tools=tools, tool_choice=tool_choice,
                     temperature=temperature, timeout=o.timeout, extra=extra)
        o.receipts.append(reply.receipt)
        msg = dict(reply.message)
        msg.setdefault("content", None)
        msg["tool_calls"] = msg.get("tool_calls") or None
        return SimpleNamespace(choices=[SimpleNamespace(message=_ns(msg), finish_reason=reply.finish_reason)],
                               meterless=reply.meterless)


class OpenAIShaped:
    """`client.chat.completions.create(...)` like the openai package, routed through Nightshift for one agent."""

    def __init__(self, agent: str = "investigator", scope: str | None = None, timeout: float = TIMEOUT_S):
        self.agent, self.scope, self.timeout = agent, scope or new_scope(f"wlwt-{agent}"), timeout
        self.receipts: list[Receipt] = []
        self.chat = SimpleNamespace(completions=_Completions(self))


def openai_client(agent: str = "investigator", scope: str | None = None, timeout: float = TIMEOUT_S) -> OpenAIShaped:
    return OpenAIShaped(agent, scope, timeout)


# --- exporting the signed receipts for the README ----------------------------------------------

_SECRETS = [
    (re.compile(r"(?i)\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}"), "api-key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "github-token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "aws-key"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"), "bearer"),
    (re.compile(r"(?i)\b(?:password|passwd|secret|api[_-]?key)\s*[:=]\s*\S{6,}"), "credential"),
    (re.compile(r"\b[A-Fa-f0-9]{40,}\b"), "long-hex"),
]


# Fields of a receipt that are hashes, signatures or ids by construction (a sha256 is not a leaked key).
_STRUCTURAL = {"manifest_hash", "llama_cpp_ref", "sig_b64", "pubkey_b64", "receipt_id", "decision_id", "request_id",
               "id", "logical_request_id"}


def receipt_text(obj, _key: str | None = None) -> str:
    """The free text of a receipt for the scan: every string value except the structural fields above (numbers,
    such as epoch-millisecond timestamps, are not text and are not scanned)."""
    if isinstance(obj, dict):
        return "\n".join(receipt_text(v, k) for k, v in obj.items() if k not in _STRUCTURAL)
    if isinstance(obj, list):
        return "\n".join(receipt_text(v, _key) for v in obj)
    return obj if isinstance(obj, str) else ""


def scan_text(text: str, extra_literals: list[str] = ()) -> list[str]:
    """Kinds of secret or personal data found in `text` (empty when clean). Personal data uses the write gate's
    detector (email, phone, address, card; conservative about dates and ids)."""
    from honest_memory.gate import detect_pii

    text = text or ""
    found = [kind for rx, kind in _SECRETS if rx.search(text)]
    found += detect_pii(text)
    found += ["token-literal" for lit in extra_literals if lit and lit in text]
    return found


def export_receipts(receipt_ids: list[str], out: str | os.PathLike, limit: int = 200,
                    apps: tuple[str, ...] = ("wlwt-investigator", "wlwt-verifier")) -> dict:
    """Write the daemon's signed receipts of one run to a jsonl file, after a secrets/PII scan.

    The signed receipt carries receipt_id and app but not the request's meterless-scope, so a run is named by the
    receipt ids its calls returned (Receipt.receipt_id, or the NIGHTSHIFT_TRACE file). A clean receipt is written
    exactly as the daemon returned it ({body, sig_b64, pubkey_b64}); one whose prompt_preview trips the scan is
    written with that field replaced and `redacted: [kinds]` beside it (its signature then covers the original)."""
    token = _token()
    want = set(receipt_ids)
    data = _get(f"/meterless/receipts?limit={min(max(limit, 1), 200)}",
                {"Authorization": f"Bearer {token}", "meterless-app": apps[0]})
    items = data.get("data") or []
    kept, redacted = [], 0
    for env in reversed(items):        # the route lists newest first; the file reads oldest first
        body = env.get("body") or {}
        r = body.get("receipt") or {}
        if r.get("app") not in apps or r.get("receipt_id") not in want:
            continue
        hits = scan_text(receipt_text(r), [token])
        if hits:
            redacted += 1
            r = {**r, "prompt_preview": f"[redacted: {', '.join(sorted(set(hits)))}]"}
            if scan_text(receipt_text(r), [token]):
                continue                 # something outside prompt_preview tripped the scan: leave the receipt out
            env = {**env, "body": {**body, "receipt": r}, "redacted": hits}
        kept.append(env)
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        for env in kept:
            fh.write(json.dumps(env, ensure_ascii=False, sort_keys=True) + "\n")
    found = {(e.get("body") or {}).get("receipt", {}).get("receipt_id") for e in kept}
    return {"path": str(path), "receipts": len(kept), "redacted": redacted, "listed": len(items),
            "missing": sorted(want - found)}


def receipt_ids_from_trace(trace: str | os.PathLike, scope_prefix: str = "") -> list[str]:
    """Receipt ids of the calls in a NIGHTSHIFT_TRACE file whose scope starts with `scope_prefix`."""
    ids = []
    for line in Path(trace).read_text().splitlines():
        r = json.loads(line)
        if str(r.get("scope", "")).startswith(scope_prefix) and r.get("receipt_id"):
            ids.append(r["receipt_id"])
    return ids


# --- command line -------------------------------------------------------------------------------

_TOOL = {"type": "function", "function": {
    "name": "current_owner", "description": "Who owns a system now.",
    "parameters": {"type": "object", "properties": {"system": {"type": "string", "description": "System name"}},
                   "required": ["system"]}}}


def clean_block(meterless: dict) -> dict:
    """The meterless block for a log file: unchanged when the scan is clean, else prompt_preview replaced."""
    token = _token()
    hits = scan_text(receipt_text(meterless), [token])
    if not hits:
        return meterless
    out = json.loads(json.dumps(meterless))
    for holder in (out.get("receipt") or {}, ((out.get("receipt_envelope") or {}).get("body") or {}).get("receipt") or {}):
        if "prompt_preview" in holder:
            holder["prompt_preview"] = f"[redacted: {', '.join(sorted(set(hits)))}]"
    if scan_text(receipt_text(out), [token]):
        return {"receipt_id": meterless.get("receipt_id"), "routed_model": meterless.get("routed_model"),
                "withheld": f"block held {', '.join(sorted(set(hits)))} outside prompt_preview"}
    out["redacted"] = sorted(set(hits))
    return out


def smoke(scope: str, agents: tuple[str, ...] = ("investigator", "verifier")) -> list[dict]:
    """One native tool call on the investigator's model and one JSON-in-text verdict on the verifier's."""
    out = []
    if "investigator" in agents:
        r = chat([{"role": "user", "content": "Who currently owns billing-api? Use the tool."}],
                 agent="investigator", scope=scope, tools=[_TOOL])
        out.append({"agent": "investigator", "finish_reason": r.finish_reason,
                    "tool_calls": r.message.get("tool_calls"), "receipt": r.receipt.to_dict(),
                    "meterless": clean_block(r.meterless)})
    if "verifier" in agents:
        r = chat([{"role": "system", "content": 'Reply with JSON only: {"verdict": "supported" | "unsupported"}.'},
                  {"role": "user", "content": 'Claim: "Luca Ostrander owns billing-api." Record ADR-0009: '
                                              '"billing-api moves to Luca Ostrander (Platform)." Is the claim '
                                              'supported?'}],
                 agent="verifier", scope=scope, max_tokens=400)
        out.append({"agent": "verifier", "finish_reason": r.finish_reason, "content": r.message.get("content"),
                    "receipt": r.receipt.to_dict(), "meterless": clean_block(r.meterless)})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m open_agent.nightshift")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sm = sub.add_parser("smoke", help="one call per agent; prints the receipts")
    sm.add_argument("--out", default=None)
    sm.add_argument("--agents", default="investigator,verifier")
    ex = sub.add_parser("export", help="write a run's signed receipts to logs/")
    ex.add_argument("--ids", default="", help="comma-separated receipt ids")
    ex.add_argument("--trace", default=None, help="a NIGHTSHIFT_TRACE file; with --scope, its calls of one run")
    ex.add_argument("--scope", default="", help="meterless-scope prefix of the run (with --trace)")
    ex.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "smoke":
        scope = new_scope("wlwt-smoke")
        res = {"scope": scope, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "calls": smoke(scope, tuple(a.strip() for a in args.agents.split(",") if a.strip()))}
        text = json.dumps(res, indent=2, ensure_ascii=False)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(text + "\n")
        print(text)
        return 0
    ids = [i for i in args.ids.split(",") if i] + (receipt_ids_from_trace(args.trace, args.scope) if args.trace else [])
    if not ids:
        ap.error("no receipt ids: give --ids or --trace")
    print(json.dumps(export_receipts(ids, args.out)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
