"""
polyclaude bridge — a mitmproxy addon that answers Claude Code's Anthropic
Messages API requests from an OpenAI-compatible backend (OpenAI, Gemini, Groq,
OpenRouter, Ollama, …), translating both directions on the wire.

Claude Code is unmodified. It sends its turn to api.anthropic.com/v1/messages;
this addon intercepts that request, translates it to the backend's format,
calls the backend, and translates the reply back into the exact Anthropic
streaming (SSE) grammar Claude Code expects.

Configured entirely via POLYCLAUDE_* environment variables (set by the CLI):
    POLYCLAUDE_BASE            backend base URL (…/v1)
    POLYCLAUDE_MODEL           main-loop model
    POLYCLAUDE_SMALL           model for the lightweight side-calls (titles etc.)
    POLYCLAUDE_KEY             API key for the backend
    POLYCLAUDE_REASONING       OpenAI reasoning effort: low|medium|high (default high)
    POLYCLAUDE_AUTOCONTINUE    1 = stitch a turn truncated at the token cap
    POLYCLAUDE_MAXTOK          output cap (default 32768)
    POLYCLAUDE_SYSTEM_APPEND_FILE   append this file to the main system prompt
    POLYCLAUDE_SYSTEM_REPLACE_FILE  replace the main system prompt with this file
    POLYCLAUDE_LOG             optional path to a wire log
"""
import json
import os
import ssl
import urllib.error
import urllib.request

from mitmproxy import http

BASE = os.environ.get("POLYCLAUDE_BASE", "").rstrip("/")
MODEL = os.environ.get("POLYCLAUDE_MODEL", "gpt-4.1")
SMALL = os.environ.get("POLYCLAUDE_SMALL", MODEL)
API_KEY = os.environ.get("POLYCLAUDE_KEY", "")
REASONING = os.environ.get("POLYCLAUDE_REASONING", "high")
AUTOCONT = os.environ.get("POLYCLAUDE_AUTOCONTINUE", "1") == "1"
MAX_TOK = int(os.environ.get("POLYCLAUDE_MAXTOK", "32768"))
MAXCONT = int(os.environ.get("POLYCLAUDE_MAXCONT", "4"))
LOG = os.environ.get("POLYCLAUDE_LOG", "")

_ssl_ctx = ssl.create_default_context()
try:
    import certifi
    _ssl_ctx = ssl.create_default_context(cafile=certifi.where())
except Exception:
    pass


def _read_file(var):
    p = os.environ.get(var, "")
    if not p:
        return ""
    try:
        with open(os.path.expanduser(p)) as f:
            return f.read().strip()
    except Exception:
        return ""


SYS_APPEND = _read_file("POLYCLAUDE_SYSTEM_APPEND_FILE")
SYS_REPLACE = _read_file("POLYCLAUDE_SYSTEM_REPLACE_FILE")

# per-tool-call state that must round-trip across turns
_SIG = {}   # Gemini 3.x thought_signature, keyed by tool-call id
_RSN = {}   # OpenAI /responses reasoning items, keyed by tool-call id


def _log(m):
    if LOG:
        try:
            with open(LOG, "a") as f:
                f.write(m + "\n")
        except Exception:
            pass


def _is_messages(flow):
    return (flow.request.pretty_host.endswith("api.anthropic.com")
            and "/v1/messages" in flow.request.path
            and not flow.request.path.endswith("count_tokens"))


def _is_count(flow):
    return (flow.request.pretty_host.endswith("api.anthropic.com")
            and flow.request.path.endswith("count_tokens"))


def _pick_model(anthropic_model):
    return SMALL if "haiku" in (anthropic_model or "") else MODEL


def _is_main_loop(body):
    m = body.get("model", "")
    tools = {t.get("name") for t in body.get("tools", []) if isinstance(t, dict)}
    return ("opus" in m) or ("sonnet" in m) or ("Bash" in tools) or ("Edit" in tools)


def _sys_to_str(system):
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        return "\n\n".join(b.get("text", "") for b in system
                           if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _apply_prompt_override(body):
    """Append/replace the main-loop system prompt from the configured files."""
    if not (SYS_APPEND or SYS_REPLACE) or not _is_main_loop(body):
        return
    sysv = body.get("system")
    if SYS_REPLACE:
        body["system"] = SYS_REPLACE
    elif SYS_APPEND:
        if isinstance(sysv, str):
            body["system"] = sysv + "\n\n" + SYS_APPEND
        elif isinstance(sysv, list):
            sysv.append({"type": "text", "text": "\n\n" + SYS_APPEND})
        else:
            body["system"] = SYS_APPEND


# ---------------------------------------------------------------------------
# Anthropic request -> OpenAI chat/completions request
# ---------------------------------------------------------------------------
def _to_openai(body):
    msgs = []
    system = _sys_to_str(body.get("system", ""))
    if system:
        msgs.append({"role": "system", "content": system})

    for m in body.get("messages", []):
        role = m.get("role")
        content = m.get("content")
        if isinstance(content, str):
            msgs.append({"role": role, "content": content})
            continue
        text_parts, tool_calls, tool_results = [], [], []
        for blk in content or []:
            if not isinstance(blk, dict):
                continue
            bt = blk.get("type")
            if bt == "text":
                text_parts.append(blk.get("text", ""))
            elif bt == "tool_use":
                tc = {"id": blk.get("id"), "type": "function",
                      "function": {"name": blk.get("name"),
                                   "arguments": json.dumps(blk.get("input", {}))}}
                sig = _SIG.get(blk.get("id"))
                if sig:
                    tc["extra_content"] = {"google": {"thought_signature": sig}}
                tool_calls.append(tc)
            elif bt == "tool_result":
                rc = blk.get("content")
                if isinstance(rc, list):
                    rc = "".join(x.get("text", "") for x in rc
                                 if isinstance(x, dict))
                elif not isinstance(rc, str):
                    rc = json.dumps(rc)
                tool_results.append((blk.get("tool_use_id"), rc or ""))
            elif bt == "image":
                text_parts.append("[image omitted]")
        if role == "assistant":
            am = {"role": "assistant", "content": "\n".join(text_parts) or None}
            if tool_calls:
                am["tool_calls"] = tool_calls
            msgs.append(am)
        else:
            for tid, rc in tool_results:
                msgs.append({"role": "tool", "tool_call_id": tid, "content": rc})
            if text_parts:
                msgs.append({"role": "user", "content": "\n".join(text_parts)})

    tgt = _pick_model(body.get("model", ""))
    out = {"model": tgt, "messages": msgs,
           "max_completion_tokens": min(int(body.get("max_tokens", MAX_TOK)),
                                        MAX_TOK)}
    reasoning = tgt.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4"))
    if not reasoning:
        if body.get("temperature") is not None:
            out["temperature"] = body["temperature"]
        if body.get("top_p") is not None:
            out["top_p"] = body["top_p"]
    if body.get("stop_sequences"):
        out["stop"] = body["stop_sequences"]

    fn_tools = [{"type": "function",
                 "function": {"name": t.get("name"),
                              "description": t.get("description", ""),
                              "parameters": t.get("input_schema",
                                                  {"type": "object"})}}
                for t in (body.get("tools") or []) if "input_schema" in t]
    if fn_tools:
        out["tools"] = fn_tools
        tc = body.get("tool_choice") or {}
        tt = tc.get("type")
        out["tool_choice"] = ("required" if tt == "any"
                              else {"type": "function",
                                    "function": {"name": tc["name"]}}
                              if tt == "tool" and tc.get("name") else "auto")
    return out


# ---------------------------------------------------------------------------
# Anthropic request -> OpenAI /responses request (tools + full reasoning)
# ---------------------------------------------------------------------------
def _to_responses(body):
    inp, seen = [], set()
    for m in body.get("messages", []):
        role = m.get("role")
        content = m.get("content")
        if isinstance(content, str):
            inp.append({"role": role, "content": content})
            continue
        buf = []
        if role == "assistant":
            for blk in content or []:
                bt = blk.get("type") if isinstance(blk, dict) else None
                if bt == "text":
                    buf.append(blk.get("text", ""))
                elif bt == "tool_use":
                    if buf:
                        inp.append({"role": "assistant", "content": "\n".join(buf)})
                        buf = []
                    for r in _RSN.get(blk.get("id"), []):
                        rid = r.get("id")
                        if rid and rid not in seen:
                            inp.append(r)
                            seen.add(rid)
                    inp.append({"type": "function_call",
                                "call_id": blk.get("id"), "name": blk.get("name"),
                                "arguments": json.dumps(blk.get("input", {}))})
            if buf:
                inp.append({"role": "assistant", "content": "\n".join(buf)})
        else:
            for blk in content or []:
                bt = blk.get("type") if isinstance(blk, dict) else None
                if bt == "text":
                    buf.append(blk.get("text", ""))
                elif bt == "image":
                    buf.append("[image omitted]")
                elif bt == "tool_result":
                    rc = blk.get("content")
                    if isinstance(rc, list):
                        rc = "".join(x.get("text", "") for x in rc
                                     if isinstance(x, dict))
                    elif not isinstance(rc, str):
                        rc = json.dumps(rc)
                    inp.append({"type": "function_call_output",
                                "call_id": blk.get("tool_use_id"),
                                "output": rc or ""})
            if buf:
                inp.append({"role": "user", "content": "\n".join(buf)})

    out = {"model": _pick_model(body.get("model", "")), "input": inp,
           "store": False, "reasoning": {"effort": REASONING},
           "include": ["reasoning.encrypted_content"],
           "max_output_tokens": min(int(body.get("max_tokens", MAX_TOK)),
                                    MAX_TOK)}
    instr = _sys_to_str(body.get("system", ""))
    if instr:
        out["instructions"] = instr
    fn = [{"type": "function", "name": t.get("name"),
           "description": t.get("description", ""),
           "parameters": t.get("input_schema", {"type": "object"})}
          for t in (body.get("tools") or []) if "input_schema" in t]
    if fn:
        out["tools"] = fn
        tc = body.get("tool_choice") or {}
        tt = tc.get("type")
        out["tool_choice"] = ("required" if tt == "any"
                              else {"type": "function", "name": tc["name"]}
                              if tt == "tool" and tc.get("name") else "auto")
    return out


def _use_responses(tgt):
    return "openai.com" in BASE and (
        os.environ.get("POLYCLAUDE_RESPONSES_FORCE") == "1"
        or tgt.startswith("gpt-5.6") or tgt.startswith("gpt-5.5"))


# ---------------------------------------------------------------------------
# HTTP to the backend
# ---------------------------------------------------------------------------
def _post(path, payload, timeout):
    req = urllib.request.Request(
        f"{BASE}/{path}", data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {API_KEY}",
                 "Content-Type": "application/json",
                 "Accept": "application/json",
                 "User-Agent": "polyclaude/0.1"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx) as r:
        return json.loads(r.read())


def _chat(payload):
    oai = _post("chat/completions", payload, 120)
    if not AUTOCONT:
        return oai
    for _ in range(MAXCONT):
        ch = (oai.get("choices") or [{}])[0]
        msg = ch.get("message", {})
        if ch.get("finish_reason") != "length" or msg.get("tool_calls"):
            break
        cont = dict(payload)
        cont["messages"] = payload["messages"] + [
            {"role": "assistant", "content": msg.get("content") or ""},
            {"role": "user", "content": "continue"}]
        nxt = _post("chat/completions", cont, 120)
        nmsg = (nxt.get("choices") or [{}])[0].get("message", {})
        oai["choices"][0]["message"]["content"] = \
            (msg.get("content") or "") + (nmsg.get("content") or "")
        oai["choices"][0]["finish_reason"] = \
            (nxt.get("choices") or [{}])[0].get("finish_reason")
        if nmsg.get("tool_calls"):
            oai["choices"][0]["message"]["tool_calls"] = nmsg["tool_calls"]
        payload = cont
    return oai


def _responses_to_oai(resp):
    text, tool_calls, pending = "", [], []
    for it in resp.get("output", []):
        t = it.get("type")
        if t == "reasoning":
            pending.append(it)
        elif t == "message":
            for c in it.get("content", []):
                if c.get("type") == "output_text":
                    text += c.get("text", "")
            pending = []
        elif t == "function_call":
            cid = it.get("call_id") or it.get("id")
            tool_calls.append({"id": cid, "type": "function",
                               "function": {"name": it.get("name"),
                                            "arguments": it.get("arguments") or "{}"}})
            if pending:
                _RSN[cid] = list(pending)
    reason = (resp.get("incomplete_details") or {}).get("reason")
    finish = ("tool_calls" if tool_calls
              else "length" if reason == "max_output_tokens" else "stop")
    u = resp.get("usage", {})
    return {"id": resp.get("id", "msg"),
            "choices": [{"message": {"content": text, "tool_calls": tool_calls},
                         "finish_reason": finish}],
            "usage": {"prompt_tokens": u.get("input_tokens", 0),
                      "completion_tokens": u.get("output_tokens", 0)}}


# ---------------------------------------------------------------------------
# OpenAI response -> Anthropic SSE
# ---------------------------------------------------------------------------
def _sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


_STOP = {"stop": "end_turn", "length": "max_tokens",
         "tool_calls": "tool_use", "content_filter": "end_turn"}


def _to_sse(oai, anthropic_model):
    ch = (oai.get("choices") or [{}])[0]
    msg = ch.get("message", {})
    text = msg.get("content") or ""
    tool_calls = msg.get("tool_calls") or []
    finish = ch.get("finish_reason", "stop")
    usage = oai.get("usage", {})
    out = b""
    out += _sse("message_start", {"type": "message_start", "message": {
        "id": oai.get("id", "msg"), "type": "message", "role": "assistant",
        "model": anthropic_model, "content": [], "stop_reason": None,
        "stop_sequence": None,
        "usage": {"input_tokens": usage.get("prompt_tokens", 0),
                  "output_tokens": 0}}})
    out += _sse("ping", {"type": "ping"})
    idx = 0
    if text:
        out += _sse("content_block_start", {"type": "content_block_start",
                    "index": idx, "content_block": {"type": "text", "text": ""}})
        out += _sse("content_block_delta", {"type": "content_block_delta",
                    "index": idx,
                    "delta": {"type": "text_delta", "text": text}})
        out += _sse("content_block_stop",
                    {"type": "content_block_stop", "index": idx})
        idx += 1
    for tc in tool_calls:
        fn = tc.get("function", {})
        tid = tc.get("id", f"toolu_{idx}")
        sig = (((tc.get("extra_content") or {}).get("google") or {})
               .get("thought_signature"))
        if sig:
            _SIG[tid] = sig
        out += _sse("content_block_start", {"type": "content_block_start",
                    "index": idx,
                    "content_block": {"type": "tool_use", "id": tid,
                                      "name": fn.get("name"), "input": {}}})
        out += _sse("content_block_delta", {"type": "content_block_delta",
                    "index": idx,
                    "delta": {"type": "input_json_delta",
                              "partial_json": fn.get("arguments") or "{}"}})
        out += _sse("content_block_stop",
                    {"type": "content_block_stop", "index": idx})
        idx += 1
    stop = "tool_use" if tool_calls else _STOP.get(finish, "end_turn")
    out += _sse("message_delta", {"type": "message_delta",
                "delta": {"stop_reason": stop, "stop_sequence": None},
                "usage": {"output_tokens": usage.get("completion_tokens", 0)}})
    out += _sse("message_stop", {"type": "message_stop"})
    return out


def _error_sse(model, message):
    return _to_sse({"id": "err", "choices": [{"message": {
        "content": f"[polyclaude error] {message}"},
        "finish_reason": "stop"}], "usage": {}}, model)


# ---------------------------------------------------------------------------
# mitmproxy hook
# ---------------------------------------------------------------------------
class Bridge:
    def request(self, flow: http.HTTPFlow):
        if _is_count(flow):
            try:
                body = json.loads(flow.request.content or b"{}")
            except Exception:
                body = {}
            flow.response = http.Response.make(
                200, json.dumps({"input_tokens": len(json.dumps(body)) // 4}
                                ).encode(), {"content-type": "application/json"})
            return
        if not _is_messages(flow):
            return
        try:
            body = json.loads(flow.request.content or b"{}")
        except Exception:
            return
        model = body.get("model", "claude-opus")
        _apply_prompt_override(body)
        tgt = _pick_model(model)
        _log(f"\n=== {model} -> {tgt}  tools={len(body.get('tools') or [])} "
             f"msgs={len(body.get('messages', []))} ===")
        try:
            if _use_responses(tgt):
                oai = _responses_to_oai(_post("responses", _to_responses(body), 300))
                _log(f">>> via /responses (reasoning={REASONING})")
            else:
                oai = _chat(_to_openai(body))
            ch = (oai.get("choices") or [{}])[0]
            _log(f">>> finish={ch.get('finish_reason')} "
                 f"usage={oai.get('usage', {})}")
            sse = _to_sse(oai, model)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:400]
            _log(f"!!! backend {e.code}: {detail}")
            sse = _error_sse(model, f"backend {e.code}: {detail}")
        except Exception as e:
            _log(f"!!! {e!r}")
            sse = _error_sse(model, repr(e))
        flow.response = http.Response.make(
            200, sse, {"content-type": "text/event-stream; charset=utf-8"})


addons = [Bridge()]
