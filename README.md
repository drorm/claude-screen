# claude-screen

A PTY wrapper for Claude Code that fixes rendering in GNU screen and preserves your terminal's native scrollback.

## What it fixes

### Rendering in GNU screen

Claude Code uses [synchronized output](https://gist.github.com/christianparpart/d8a62cc1ab659194337d73e399004036) (DEC mode 2026) for flicker-free terminal rendering. tmux supports this; GNU screen does not. Screen silently discards the sync markers, so the raw escape sequences execute immediately. When the cursor is near the bottom of the terminal, bare `\r\n` sequences in the output trigger unwanted scrolling, causing cumulative drift that corrupts the display.

This is especially visible in nested screen sessions (e.g., local screen → SSH → remote screen → Claude Code).

### Scrollback preservation

Claude Code emits `\e[3J` (Erase Scrollback) as part of its render cycle, which destroys your terminal's native scrollback. This affects every terminal — not just screen — and is tracked upstream in [anthropics/claude-code#2479](https://github.com/anthropics/claude-code/issues/2479) and [#42670](https://github.com/anthropics/claude-code/issues/42670). The result: shell output from before `claude` launched is wiped, and you can't scroll up to see earlier parts of the session.

`claude-screen` absorbs Claude's output in a virtual terminal, so the scrollback-erasing escapes never reach the real terminal. At startup it pushes any visible shell output into native scrollback and emits a boundary marker:

```
----------------------------------------------------------------

                    Clear screen replacement

----------------------------------------------------------------
```

so when you scroll up you can see exactly where the shell ends and the claude session begins.

## How it works

`claude-screen` sits between Claude Code and your terminal as a PTY proxy. It runs a virtual terminal emulator ([pyte](https://github.com/selectel/pyte)) that absorbs Claude's full output — sync blocks, cursor movement, colors, everything. Instead of forwarding the raw escape sequences, it diffs the virtual screen against what was last rendered and sends only the changed lines using basic escape sequences that screen handles correctly.

When lines scroll off the top of the virtual screen, they are forwarded to the real terminal's native scrollback buffer (screen's copy mode, your terminal's scrollbar) so you can still scroll back through earlier output.

This is the same approach used by tmux internally and by [claude-chill](https://github.com/davidbeesley/claude-chill) (a Rust implementation).

## Install

Requires Python 3.7+ and pyte:

```bash
pip install pyte
```

Then clone or download `claude-screen.py`:

```bash
git clone https://github.com/youruser/claude-screen.git
```

If `pip` isn't available, you can download the pyte and wcwidth wheels directly and extract them into the same directory as `claude-screen.py`:

```bash
cd claude-screen
python3 -c "
import urllib.request, zipfile, io
for url in [
    'https://files.pythonhosted.org/packages/68/5a/199c59e0a824a3db2b89c5d2dade7ab5f9624dbf6448dc291b46d5ec94d3/wcwidth-0.6.0-py3-none-any.whl',
    'https://files.pythonhosted.org/packages/59/d0/bb522283b90853afbf506cd5b71c650cf708829914efd0003d615cf426cd/pyte-0.8.2-py3-none-any.whl',
]:
    data = urllib.request.urlopen(url).read()
    zipfile.ZipFile(io.BytesIO(data)).extractall('.')
    print(f'Extracted {url.split(\"/\")[-1]}')
"
```

## Usage

Run it instead of `claude`:

```bash
python3 claude-screen.py
```

Pass arguments through to Claude Code:

```bash
python3 claude-screen.py --resume
```

For debug logging:

```bash
CLAUDE_SCREEN_DEBUG=1 python3 claude-screen.py
# logs written to /tmp/claude-screen.log
```

### Shell alias

Aliasing `claude` to the wrapper is the recommended setup — arguments pass through, so commands like `claude --resume 3e1ba691-...` work unchanged.

bash / zsh (`.bashrc` / `.zshrc`):

```bash
alias claude='python3 /path/to/claude-screen.py'
```

tcsh / csh (`.tcshrc` / `.cshrc`):

```tcsh
alias claude /path/to/claude-screen.py
```

(The script has a `#!/usr/bin/env python3` shebang; `chmod +x claude-screen.py` once and you can invoke it directly.)

## How it compares

| Approach | Sync rendering | Dependencies | Complexity |
|---|---|---|---|
| tmux | Native support | None | Just use tmux |
| [claude-chill](https://github.com/davidbeesley/claude-chill) | VT emulator + diff | Rust binary (~4K lines) | Feature-rich (history, lookback) |
| **claude-screen** | VT emulator + diff | Python + pyte (~300 lines) | Minimal, single-file |

## Security

`claude-screen` sits between Claude Code and your terminal, so it sees every keystroke you type and everything Claude writes back. You should only run code that does that after you've looked at it.

The wrapper is designed to be easy to audit:

- One file (`claude-screen.py`, ~400 lines of Python).
- One dependency (`pyte`, a widely-used VT emulator).
- No network calls, no filesystem writes outside the optional debug log (`/tmp/claude-screen.log`, only when `CLAUDE_SCREEN_DEBUG=1`), no subprocesses other than `claude` itself.

You can read the source directly, or paste it into Claude Code (or another LLM) and ask it to summarize behavior and flag anything surprising. LLM review isn't a security guarantee — it can miss subtle issues — but it's a low-effort way to get a second look before you trust the wrapper with your session.

## Limitations

- pyte doesn't support SGR 2 (dim/faint text), so dim content renders at normal brightness.
- True color (24-bit) passthrough depends on your screen version supporting `38;2;r;g;b`. Most 256-color values are mapped back correctly.
- No in-proxy lookback mode (unlike claude-chill) — use your terminal's native scrollback.

## License

MIT
