"""Tiny dependency-free progress bar. TTY-aware (falls back to periodic lines)."""
import sys
import time


class Progress:
    def __init__(self, total, label="scoring", width=28, stream=sys.stderr):
        self.total = max(total, 1)
        self.label = label
        self.width = width
        self.stream = stream
        self.n = 0
        self.start = time.monotonic()
        self.tty = getattr(stream, "isatty", lambda: False)()
        self._last_line_pct = -1
        self._render()

    def advance(self, k=1, suffix=""):
        self.n += k
        self._render(suffix)

    def _render(self, suffix=""):
        frac = min(self.n / self.total, 1.0)
        elapsed = time.monotonic() - self.start
        eta = (elapsed / frac - elapsed) if frac > 0 else 0
        pct = int(frac * 100)
        bar = "█" * int(frac * self.width)
        bar += "░" * (self.width - len(bar))
        line = (f"{self.label} |{bar}| {self.n}/{self.total} "
                f"({pct}%) eta {self._fmt(eta)}")
        if suffix:
            line += f"  {suffix}"
        if self.tty:
            self.stream.write("\r" + line + "\033[K")
            self.stream.flush()
        elif pct != self._last_line_pct and pct % 10 == 0:
            # non-interactive (launchd): one line every ~10%
            self.stream.write(line + "\n")
            self.stream.flush()
            self._last_line_pct = pct

    def close(self, msg=""):
        if self.tty:
            self.stream.write("\r\033[K")
            self.stream.flush()
        if msg:
            self.stream.write(msg + "\n")
            self.stream.flush()

    @staticmethod
    def _fmt(sec):
        sec = int(sec)
        if sec < 60:
            return f"{sec}s"
        return f"{sec // 60}m{sec % 60:02d}s"
