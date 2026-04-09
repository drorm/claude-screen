# claude-screen

Fix Claude Code rendering in GNU screen and other environments.

## The problem

Claude Code uses [synchronized output](https://gist.github.com/christianparpart/d8a62cc1ab659194337d73e399004036) (DEC mode 2026) for flicker-free terminal rendering. tmux supports this; GNU screen does not. Screen silently discards the sync markers, so the raw escape sequences execute immediately. When the cursor is near the bottom of the terminal, bare `\r\n` sequences in the output trigger unwanted scrolling, causing cumulative drift that corrupts the display.

This is especially visible in nested screen sessions (e.g., local screen -> SSH -> remote screen -> Claude Code).

## How it works

`claude-screen` sits between Claude Code and your terminal as a PTY proxy. It runs a virtual terminal emulator ([pyte](https://github.com/selectel/pyte)) that absorbs Claude's full output — sync blocks, cursor movement, colors, everything. Instead of forwarding the raw escape sequences, it diffs the virtual screen against what was last rendered and sends only the changed lines using basic escape sequences that screen handles correctly.

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

Add to your `.bashrc` / `.zshrc`:

```bash
alias claude='python3 /path/to/claude-screen.py'
```

## How it compares

| Approach | Sync rendering | Dependencies | Complexity |
|---|---|---|---|
| tmux | Native support | None | Just use tmux |
| [claude-chill](https://github.com/davidbeesley/claude-chill) | VT emulator + diff | Rust binary (~4K lines) | Feature-rich (history, lookback) |
| **claude-screen** | VT emulator + diff | Python + pyte (~300 lines) | Minimal, single-file |

## Limitations

- pyte doesn't support SGR 2 (dim/faint text), so dim content renders at normal brightness.
- True color (24-bit) passthrough depends on your screen version supporting `38;2;r;g;b`. Most 256-color values are mapped back correctly.
- No scrollback history or lookback mode (unlike claude-chill).

## License

MIT
