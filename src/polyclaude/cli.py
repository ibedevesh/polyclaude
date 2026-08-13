"""polyclaude CLI — start the bridge and launch Claude Code on a chosen model."""
import argparse
import atexit
import importlib.util
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from . import __version__
from .providers import PROVIDERS

CA = Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"


def _load_identity_file(name_or_path):
    """Load a JSON {old: new} identity map by bundled name or filesystem path."""
    import json
    p = Path(name_or_path).expanduser()
    if not p.exists():
        bundled = Path(__file__).parent / "identities" / f"{name_or_path}.json"
        if bundled.exists():
            p = bundled
        else:
            avail = ", ".join(sorted(
                f.stem for f in (Path(__file__).parent / "identities").glob("*.json"))
            ) or "(none)"
            _die(f"identity file '{name_or_path}' not found. Bundled: {avail} "
                 f"(or pass a path to a .json file)")
    try:
        data = json.loads(p.read_text())
    except Exception as e:
        _die(f"could not parse identity file {p}: {e}")
    if not isinstance(data, dict):
        _die(f"identity file {p} must be a JSON object of {{\"old\": \"new\"}}")
    return data


def _die(msg, code=1):
    print(f"polyclaude: {msg}", file=sys.stderr)
    sys.exit(code)


def _find(exe):
    """Locate an executable, preferring the one next to our interpreter."""
    local = Path(sys.executable).parent / exe
    if local.exists():
        return str(local)
    return shutil.which(exe)


def _resolve_key(prov):
    for var in prov["key_env"]:
        v = os.environ.get(var)
        if v:
            return v
    return None


def _profile_path(name):
    # a path wins; otherwise look up a bundled profile by name
    if os.path.sep in name or name.endswith(".md"):
        p = Path(name).expanduser()
        return str(p) if p.exists() else None
    bundled = Path(__file__).parent / "profiles" / f"{name}.md"
    return str(bundled) if bundled.exists() else None


def _logged_in():
    """True if the user has a claude.ai session (so we must NOT also set a key)."""
    try:
        if sys.platform == "darwin":
            r = subprocess.run(
                ["security", "find-generic-password", "-s",
                 "Claude Code-credentials"], capture_output=True)
            if r.returncode == 0:
                return True
    except Exception:
        pass
    return (Path.home() / ".claude" / ".credentials.json").exists()


def _free_port(start):
    for p in range(start, start + 40):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    return start


def _wait_listen(port, timeout=25):
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    passthrough = []
    if "--" in argv:
        i = argv.index("--")
        passthrough = argv[i + 1:]
        argv = argv[:i]

    ap = argparse.ArgumentParser(
        prog="polyclaude",
        description="Use OpenAI, Gemini, and any OpenAI-compatible model in Claude Code.",
        epilog="Examples:\n"
               "  polyclaude --gemini\n"
               "  polyclaude --openai --model gpt-4.1\n"
               "  polyclaude --gemini --profile datascience\n"
               "  polyclaude --openai -- -p \"one-shot prompt\"",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    for name in PROVIDERS:
        g.add_argument(f"--{name}", dest="provider", action="store_const",
                       const=name, help=f"use {name}")
    ap.add_argument("--provider", dest="provider2",
                    help="provider name (alternative to the flags above)")
    ap.add_argument("--model", help="model override")
    ap.add_argument("--profile", help="specialize the system prompt "
                    "(bundled name or path to a .md file)")
    ap.add_argument("--system", help="replace the whole main system prompt with this file")
    # --- identity scrub (works with any provider, incl. --claude passthrough) ---
    ap.add_argument("--identity", action="append", default=[], metavar="OLD=NEW",
                    help="literal identity rewrite; repeatable or comma-separated "
                         "(e.g. --identity atlys=probo,Devesh=Alex)")
    ap.add_argument("--identity-file", metavar="NAME|PATH",
                    help="load a JSON {old:new} identity map (bundled name or path)")
    ap.add_argument("--as-email", metavar="EMAIL",
                    help="replace EVERY email in the payload with this (no need to "
                         "know the real one)")
    ap.add_argument("--as-user", metavar="NAME",
                    help="replace EVERY home-dir username (/Users/x) with this")
    ap.add_argument("--scrub-headers", action="store_true",
                    help="also strip x-stainless-* telemetry headers + user-agent")
    ap.add_argument("--inspect", nargs="?", const="/tmp/polyclaude-identity.json",
                    default=None, metavar="PATH",
                    help="log every identity signal each request carries")
    ap.add_argument("--reasoning", choices=["low", "medium", "high"],
                    help="OpenAI reasoning depth (default high)")
    ap.add_argument("--hue", type=float, default=110.0,
                    help="accent hue for the branding, degrees (default 110 = green)")
    ap.add_argument("--resume", nargs="?", const="", default=None, metavar="SESSION",
                    help="resume a previous session (forwarded to Claude Code)")
    ap.add_argument("--continue", dest="cont", action="store_true",
                    help="continue the most recent session (forwarded to Claude Code)")
    ap.add_argument("--port", type=int, default=8118, help="proxy port (default 8118)")
    ap.add_argument("--list", action="store_true", help="list providers and exit")
    ap.add_argument("--verbose", action="store_true", help="print the wire log path")
    ap.add_argument("--version", action="version",
                    version=f"polyclaude {__version__}")
    args = ap.parse_args(argv)

    if args.list:
        for n, p in PROVIDERS.items():
            key = (p["key_env"][0] if p["key_env"] else "(none)")
            model = p["model"] or ("real Anthropic (passthrough)" if p.get("passthrough") else "")
            print(f"  --{n:<11} default {model:<28} key: {key}")
        return

    provider = args.provider or args.provider2 or "gemini"
    if provider not in PROVIDERS:
        _die(f"unknown provider '{provider}'. Try: {', '.join(PROVIDERS)}")
    prov = PROVIDERS[provider]

    key = _resolve_key(prov)
    if prov["key_env"] and not key:
        _die(f"no API key found. Set one of {prov['key_env']} in your "
             f"environment.\n  {prov['help']}")

    claude = shutil.which("claude")
    if not claude:
        _die("the `claude` CLI is not installed or not on PATH.\n"
             "  Install: https://docs.claude.com/claude-code")
    mitmdump = _find("mitmdump")
    if not mitmdump:
        _die("mitmproxy not found (it is a dependency; try reinstalling polyclaude).")

    passthrough_mode = prov.get("passthrough", False)
    if passthrough_mode and not _logged_in() and not os.environ.get("ANTHROPIC_API_KEY"):
        _die("--claude talks to the real Anthropic model, so it needs real auth.\n"
             "  Log into claude.ai (run `claude` once) or set ANTHROPIC_API_KEY.")

    # assemble the identity rewrite map
    id_map = {}
    for item in args.identity:
        for pair in item.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                if k.strip():
                    id_map[k.strip()] = v.strip()
    if args.identity_file:
        for k, v in _load_identity_file(args.identity_file).items():
            id_map[str(k)] = str(v)
    scrub_on = bool(id_map or args.as_email or args.as_user)

    bridge = importlib.util.find_spec("polyclaude.bridge").origin
    model = args.model or prov["model"] or ("claude" if passthrough_mode else "")
    port = _free_port(args.port)
    log = "/tmp/polyclaude.log"

    env = os.environ.copy()
    env.update({
        "POLYCLAUDE_BASE": prov["base"],
        "POLYCLAUDE_MODEL": model,
        "POLYCLAUDE_SMALL": prov["small"],
        "POLYCLAUDE_KEY": key or "none",
        "POLYCLAUDE_REASONING": args.reasoning or "high",
        "POLYCLAUDE_AUTOCONTINUE": "1",
        "POLYCLAUDE_LOG": log,
    })
    if passthrough_mode:
        env["POLYCLAUDE_PASSTHROUGH"] = "1"
    if scrub_on:
        env["POLYCLAUDE_SCRUB"] = "1"
        if id_map:
            env["POLYCLAUDE_IDENTITY_MAP"] = ",".join(f"{k}={v}" for k, v in id_map.items())
        if args.as_email:
            env["POLYCLAUDE_ID_EMAIL"] = args.as_email
        if args.as_user:
            env["POLYCLAUDE_ID_HOME"] = args.as_user
    if args.inspect:
        env["POLYCLAUDE_SCRUB_LOG"] = os.path.expanduser(args.inspect)
    if args.scrub_headers:
        env["POLYCLAUDE_SCRUB_HEADERS"] = "1"
    if args.system:
        sp = Path(args.system).expanduser()
        if not sp.exists():
            _die(f"--system file not found: {args.system}")
        env["POLYCLAUDE_SYSTEM_REPLACE_FILE"] = str(sp)
    elif args.profile:
        pp = _profile_path(args.profile)
        if not pp:
            avail = ", ".join(sorted(
                p.stem for p in (Path(__file__).parent / "profiles").glob("*.md")))
            _die(f"unknown profile '{args.profile}'. Bundled: {avail} "
                 f"(or pass a path to a .md file)")
        env["POLYCLAUDE_SYSTEM_APPEND_FILE"] = pp

    # a proxy env must NOT leak into the proxy's own upstream call
    for v in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        env.pop(v, None)

    print(f"polyclaude {__version__}  ·  {provider}  ·  {model}")
    open(log, "w").close()
    proxy = subprocess.Popen(
        [mitmdump, "-q", "-s", bridge, "--listen-host", "127.0.0.1",
         "--listen-port", str(port), "--allow-hosts", r"anthropic\.com"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _cleanup():
        if proxy.poll() is None:
            proxy.terminate()
            try:
                proxy.wait(timeout=5)
            except Exception:
                proxy.kill()
    atexit.register(_cleanup)

    if not _wait_listen(port):
        _cleanup()
        _die("the bridge proxy did not start in time.")
    # mitmproxy creates its CA on first start; wait for it
    for _ in range(50):
        if CA.exists():
            break
        time.sleep(0.1)
    if not CA.exists():
        _cleanup()
        _die("mitmproxy CA not found; run `mitmdump` once to generate it, then retry.")

    if args.verbose:
        print(f"  wire log: tail -f {log}")
    print(f"  launching Claude Code (Ctrl-C to exit)\n")

    # forward session flags to Claude Code (it has native --resume/--continue)
    fwd = []
    if args.cont:
        fwd.append("--continue")
    if args.resume is not None:
        fwd.append("--resume")
        if args.resume:
            fwd.append(args.resume)
    passthrough = fwd + passthrough

    run_env = os.environ.copy()
    run_env["HTTPS_PROXY"] = f"http://127.0.0.1:{port}"
    run_env["NODE_EXTRA_CA_CERTS"] = str(CA)
    # Claude Code refuses to call the API with no credential ("Please run
    # /login"). If the user is already logged into claude.ai, leave auth alone
    # (setting a key too triggers a conflict warning); otherwise supply a dummy
    # key — the bridge answers regardless and never checks it.
    if not _logged_in() and "ANTHROPIC_API_KEY" not in os.environ:
        run_env["ANTHROPIC_API_KEY"] = "sk-polyclaude-bridge"
    cmd = [claude, *passthrough]
    # brand the UI for real interactive sessions; skip only for non-interactive
    # / piped runs (`-p`), which have no welcome screen to brand.
    interactive = not any(a in ("-p", "--print") for a in passthrough)
    # in passthrough it IS real Claude — leave its branding untouched
    reskin_active = (interactive and sys.stdin.isatty()
                     and not passthrough_mode
                     and os.environ.get("POLYCLAUDE_NO_RESKIN") != "1")

    try:
        if reskin_active:
            os.environ["POLYCLAUDE_HUE"] = str(args.hue)
            os.environ["POLYCLAUDE_REBRAND"] = (
                "Claude Code=polyclaude,"
                f"Claude Opus 5={model},Opus 5={model},Sonnet 4.5={model}")
            from . import reskin
            rc = reskin.run(cmd, env=run_env)
        else:
            rc = subprocess.call(cmd, env=run_env)
    except KeyboardInterrupt:
        rc = 130
    _cleanup()
    sys.exit(rc)


if __name__ == "__main__":
    main()
