#!/usr/bin/env python3
"""
claude-screen — Fix Claude Code rendering in GNU screen.

GNU screen doesn't support synchronized output (DEC mode 2026), which
Claude Code relies on for flicker-free rendering. This wrapper runs a
virtual terminal emulator (pyte) that absorbs Claude's raw output, then
sends only per-line diffs to the real terminal using basic escape
sequences that screen handles correctly.

Requires: pyte (pip install pyte)

Usage:
    python3 claude-screen.py [claude args...]
    CLAUDE_SCREEN_DEBUG=1 python3 claude-screen.py   # log to /tmp/claude-screen.log
"""

import fcntl, os, pty, re, select, signal, struct, sys, termios, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pyte

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SYNC_OPEN = b'\x1b[?2026h'
SYNC_CLOSE = b'\x1b[?2026l'

RENDER_DELAY = 0.005       # seconds to wait after output before rendering
SYNC_RENDER_DELAY = 0.050  # longer delay inside sync blocks (more data coming)

# Sequences pyte misinterprets — strip before feeding.
#   \x1b[>4m   XTMODKEYS  → pyte reads as SGR 4 (underline)
#   \x1b[<u    Kitty kbd  → "u" leaks into screen buffer
#   \x1b[>0q   XTVERSION  → junk in buffer
#   \x1b[c     DA1 query  → triggers pyte response handler
#   OSC (\x1b]...) titles → not needed for rendering
_STRIP = re.compile(
    rb'\x1b\[[><=][0-9;]*[a-zA-Z]'
    rb'|\x1b\[c'
    rb'|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)'
)

# ---------------------------------------------------------------------------
# Color conversion: pyte hex → ANSI SGR
# ---------------------------------------------------------------------------

_NAMED = {
    'black': 0, 'red': 1, 'green': 2, 'brown': 3, 'yellow': 3,
    'blue': 4, 'magenta': 5, 'cyan': 6, 'white': 7,
    'brightblack': 8, 'brightred': 9, 'brightgreen': 10, 'brightyellow': 11,
    'brightblue': 12, 'brightmagenta': 13, 'brightcyan': 14, 'brightwhite': 15,
}

# Reverse map: hex color string → 256-color index.
# pyte converts 256-color indices to hex using the xterm palette.
_HEX256 = {}
_CUBE6 = [0x00, 0x5f, 0x87, 0xaf, 0xd7, 0xff]

def _init_colors():
    std16 = [
        (0x00,0x00,0x00), (0x80,0x00,0x00), (0x00,0x80,0x00), (0x80,0x80,0x00),
        (0x00,0x00,0x80), (0x80,0x00,0x80), (0x00,0x80,0x80), (0xc0,0xc0,0xc0),
        (0x80,0x80,0x80), (0xff,0x00,0x00), (0x00,0xff,0x00), (0xff,0xff,0x00),
        (0x00,0x00,0xff), (0xff,0x00,0xff), (0x00,0xff,0xff), (0xff,0xff,0xff),
    ]
    for i, (r, g, b) in enumerate(std16):
        _HEX256[f'{r:02x}{g:02x}{b:02x}'] = i
    for i in range(216):
        r, g, b = _CUBE6[i // 36], _CUBE6[(i // 6) % 6], _CUBE6[i % 6]
        _HEX256[f'{r:02x}{g:02x}{b:02x}'] = 16 + i
    for i in range(24):
        v = 8 + 10 * i
        _HEX256[f'{v:02x}{v:02x}{v:02x}'] = 232 + i

_init_colors()


def _sgr_color(color, bg=False):
    """Convert a pyte color value to an SGR parameter fragment."""
    if color == 'default':
        return '49' if bg else '39'
    base = 40 if bg else 30
    if color in _NAMED:
        idx = _NAMED[color]
        return str(base + idx) if idx < 8 else str(base + 60 + idx - 8)
    color = color.lower()
    if color in _HEX256:
        return f'{base+8};5;{_HEX256[color]}'
    r, g, b = int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)
    return f'{base+8};2;{r};{g};{b}'


def _sgr(ch):
    """Full SGR sequence string for a pyte Char."""
    p = []
    if ch.bold:          p.append('1')
    if ch.italics:       p.append('3')
    if ch.underscore:    p.append('4')
    if ch.blink:         p.append('5')
    if ch.reverse:       p.append('7')
    if ch.strikethrough: p.append('9')
    p.append(_sgr_color(ch.fg))
    p.append(_sgr_color(ch.bg, bg=True))
    return '\x1b[' + ';'.join(p) + 'm'

# ---------------------------------------------------------------------------
# Diff renderer
# ---------------------------------------------------------------------------

def render(screen, dirty):
    """Render dirty lines. Returns bytes to write to the real terminal."""
    out = []
    rows, cols = screen.lines, screen.columns
    default = screen.default_char

    for y in sorted(dirty):
        if y >= rows:
            continue
        row = screen.buffer.get(y, {})

        # Find rightmost non-trivial cell
        end = -1
        for x in range(cols):
            ch = row.get(x, default)
            if (ch.data != ' ' or ch.fg != 'default' or ch.bg != 'default'
                    or ch.bold or ch.reverse):
                end = x

        out.append(f'\x1b[{y+1};1H')
        if end < 0:
            out.append('\x1b[0m\x1b[K')
            continue

        prev = None
        for x in range(end + 1):
            ch = row.get(x, default)
            s = _sgr(ch)
            if s != prev:
                out.append(s)
                prev = s
            out.append(ch.data)
        out.append('\x1b[0m\x1b[K')

    # Cursor
    out.append(f'\x1b[{screen.cursor.y+1};{screen.cursor.x+1}H')
    if pyte.modes.DECTCEM in screen.mode:
        out.append('\x1b[?25h')
    else:
        out.append('\x1b[?25l')

    return ''.join(out).encode()

# ---------------------------------------------------------------------------
# Terminal helpers
# ---------------------------------------------------------------------------

def get_winsize(fd):
    buf = fcntl.ioctl(fd, termios.TIOCGWINSZ, b'\x00' * 8)
    return struct.unpack('HHHH', buf)[:2]   # (rows, cols)


def set_winsize(fd, rows, cols):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))

# ---------------------------------------------------------------------------
# Main proxy loop
# ---------------------------------------------------------------------------

_log_fd = None

def _log(msg):
    if _log_fd:
        _log_fd.write(f'[{time.time():.3f}] {msg}\n'.encode())


def _strip_sync(data):
    """Remove sync markers, return (cleaned_bytes, currently_in_sync)."""
    in_sync = False
    for marker, flag in [(SYNC_OPEN, True), (SYNC_CLOSE, False)]:
        while marker in data:
            i = data.find(marker)
            data = data[:i] + data[i + len(marker):]
            in_sync = flag
    return data, in_sync


def main():
    global _log_fd
    if os.environ.get('CLAUDE_SCREEN_DEBUG'):
        _log_fd = open('/tmp/claude-screen.log', 'wb', buffering=0)

    rows, cols = get_winsize(sys.stdout.fileno())
    _log(f'terminal {rows}x{cols}')

    master_fd, slave_fd = pty.openpty()
    set_winsize(slave_fd, rows, cols)

    pid = os.fork()
    if pid == 0:
        os.setsid()
        fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
        for fd_num in (0, 1, 2):
            os.dup2(slave_fd, fd_num)
        os.close(master_fd)
        os.close(slave_fd)
        os.execvp('claude', ['claude'] + sys.argv[1:])

    os.close(slave_fd)

    screen = pyte.Screen(cols, rows)
    stream = pyte.Stream(screen)
    in_sync = False
    last_out_t = None
    pending = False

    def on_winsize(sig, frame):
        nonlocal screen, stream
        r, c = get_winsize(sys.stdout.fileno())
        set_winsize(master_fd, r, c)
        screen.resize(r, c)
        _log(f'resize {r}x{c}')

    signal.signal(signal.SIGWINCH, on_winsize)

    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    old_tty = termios.tcgetattr(stdin_fd)

    try:
        import tty
        tty.setraw(stdin_fd)

        while True:
            delay = (SYNC_RENDER_DELAY if in_sync else RENDER_DELAY)
            if pending and last_out_t:
                wait = max(0, delay - (time.time() - last_out_t))
            else:
                wait = 0.05

            try:
                ready, _, _ = select.select([master_fd, stdin_fd], [], [], wait)
            except (ValueError, OSError):
                break

            if stdin_fd in ready:
                try:
                    d = os.read(stdin_fd, 4096)
                    if d:
                        os.write(master_fd, d)
                except OSError:
                    break

            if master_fd in ready:
                try:
                    data = os.read(master_fd, 65536)
                except OSError:
                    break

                data, in_sync = _strip_sync(data)
                data = _STRIP.sub(b'', data)

                if data:
                    try:
                        stream.feed(data.decode('utf-8', errors='replace'))
                    except Exception as e:
                        _log(f'feed error: {e}')
                last_out_t = time.time()
                pending = True

            if pending and last_out_t and (time.time() - last_out_t) >= delay:
                dirty = screen.dirty
                if dirty:
                    out = render(screen, dirty)
                    screen.dirty.clear()
                    if out:
                        os.write(stdout_fd, out)
                pending = False

            try:
                if os.waitpid(pid, os.WNOHANG)[0]:
                    if screen.dirty:
                        os.write(stdout_fd, render(screen, screen.dirty))
                    break
            except ChildProcessError:
                break
    finally:
        termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_tty)
        os.write(stdout_fd, b'\x1b[?25h\x1b[0m\r\n')
        if _log_fd:
            _log_fd.close()


if __name__ == '__main__':
    main()
