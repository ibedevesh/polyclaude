"""
Optional cosmetic reskin: run Claude Code inside a pseudo-terminal and rewrite
its output as it's drawn — recolor the accent, relabel "Claude Code" to
"polyclaude", and correct the displayed model name (which otherwise still reads
as a Claude model even though a different model is answering).

This is display-only. The binary is not modified; your keystrokes pass through
untouched. Enabled with `--reskin`.

Config via env (set by the CLI):
    POLYCLAUDE_REBRAND   "old=new,old2=new2"   text substitutions
    POLYCLAUDE_HUE       degrees (0-360)        rotate the accent colour
"""
import colorsys
import fcntl
import os
import pty
import re
import select
import signal
import struct
import sys
import termios
import tty

HUE = float(os.environ.get("POLYCLAUDE_HUE", "0"))
_COLOR = HUE != 0


def _parse(spec):
    pairs = []
    for chunk in spec.split(","):
        if "=" in chunk:
            o, n = chunk.split("=", 1)
            o = o.strip()
            if o:
                pairs.append((o.encode(), n.strip().encode()))
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    return pairs


REBRAND = _parse(os.environ.get("POLYCLAUDE_REBRAND", ""))
_MAXKEY = max((len(o) for o, _ in REBRAND), default=1)

_BASIC = {30: (0, 0, 0), 31: (205, 0, 0), 32: (0, 205, 0), 33: (205, 205, 0),
          34: (0, 0, 238), 35: (205, 0, 205), 36: (0, 205, 205), 37: (229, 229, 229),
          90: (127, 127, 127), 91: (255, 0, 0), 92: (0, 255, 0), 93: (255, 255, 0),
          94: (92, 92, 255), 95: (255, 0, 255), 96: (0, 255, 255), 97: (255, 255, 255)}


def _xterm(n):
    if n < 16:
        return _BASIC.get(30 + n if n < 8 else 90 + (n - 8), (128, 128, 128))
    if n <= 231:
        n -= 16
        f = lambda c: 0 if c == 0 else 55 + 40 * c
        return f(n // 36), f((n // 6) % 6), f(n % 6)
    v = 8 + (n - 232) * 10
    return v, v, v


def _shift(r, g, b):
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    h = (h + HUE / 360.0) % 1.0
    nr, ng, nb = colorsys.hsv_to_rgb(h, s, v)
    return int(nr * 255), int(ng * 255), int(nb * 255)


_SGR = re.compile(rb"\x1b\[([0-9;]*)m")


def _map_params(pb):
    if not pb:
        return pb
    try:
        toks = [int(x) if x else 0 for x in pb.split(b";")]
    except ValueError:
        return pb
    out, i = [], 0
    while i < len(toks):
        t = toks[i]
        if t in (38, 48) and i + 1 < len(toks) and toks[i + 1] == 5:
            out += [t, 2, *_shift(*_xterm(toks[i + 2] if i + 2 < len(toks) else 0))]
            i += 3
        elif t in (38, 48) and i + 1 < len(toks) and toks[i + 1] == 2:
            r = toks[i + 2] if i + 2 < len(toks) else 0
            g = toks[i + 3] if i + 3 < len(toks) else 0
            b = toks[i + 4] if i + 4 < len(toks) else 0
            out += [t, 2, *_shift(r, g, b)]
            i += 5
        elif t in _BASIC:
            out += [38, 2, *_shift(*_BASIC[t])]
            i += 1
        elif (t - 10) in _BASIC:
            out += [48, 2, *_shift(*_BASIC[t - 10])]
            i += 1
        else:
            out.append(t)
            i += 1
    return b";".join(str(x).encode() for x in out)


def _rewrite(buf):
    if _COLOR:
        buf = _SGR.sub(lambda m: b"\x1b[" + _map_params(m.group(1)) + b"m", buf)
    for old, new in REBRAND:
        buf = buf.replace(old, new)
    return buf


_INCOMPLETE = re.compile(rb"\x1b(\[[0-9;]*)?$")


def _holdback(buf):
    hb = 0
    maxk = min(_MAXKEY - 1, len(buf))
    for k in range(maxk, 0, -1):
        if any(len(o) > k and o.startswith(buf[-k:]) for o, _ in REBRAND):
            hb = k
            break
    m = _INCOMPLETE.search(buf)
    if m:
        hb = max(hb, len(buf) - m.start())
    return min(hb, len(buf))


def _winsize(fd):
    try:
        sz = fcntl.ioctl(sys.stdin.fileno(), termios.TIOCGWINSZ, b"\x00" * 8)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, sz)
    except Exception:
        pass


def run(cmd, env=None):
    """Run cmd (list) inside a PTY, rewriting its output. Returns exit code."""
    pid, master = pty.fork()
    if pid == 0:
        if env:
            os.execvpe(cmd[0], cmd, env)
        os.execvp(cmd[0], cmd)
        os._exit(127)
    _winsize(master)
    in_fd = sys.stdin.fileno()
    old = None
    try:
        old = termios.tcgetattr(in_fd)
        tty.setraw(in_fd)
    except Exception:
        pass
    signal.signal(signal.SIGWINCH, lambda *a: _winsize(master))
    carry = b""
    try:
        while True:
            r, _, _ = select.select([master, in_fd], [], [])
            if master in r:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                if not data:
                    break
                buf = carry + data
                h = _holdback(buf)
                safe = buf[:len(buf) - h] if h else buf
                carry = buf[len(buf) - h:] if h else b""
                os.write(sys.stdout.fileno(), _rewrite(safe))
            if in_fd in r:
                try:
                    inp = os.read(in_fd, 65536)
                except OSError:
                    break
                if not inp:
                    break
                os.write(master, inp)
    finally:
        if carry:
            os.write(sys.stdout.fileno(), _rewrite(carry))
        if old is not None:
            termios.tcsetattr(in_fd, termios.TCSADRAIN, old)
        try:
            os.close(master)
        except Exception:
            pass
        _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode") else 0
