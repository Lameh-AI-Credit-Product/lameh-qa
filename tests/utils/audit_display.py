"""
A pinned terminal header, for long-running steps
================================================

Splits the terminal in two: a fixed header at the top that this process
repaints, and everything below it left alone for a child process to scroll
through normally.

This exists for the Sector Analysis audit, whose first two steps run for tens
of minutes. Both are chatty enough that you cannot tell from the output alone
whether they are 5% or 95% through, and the one line that said which step was
running scrolled away within seconds of starting it.

How the split works
-------------------
A DEC scroll region (`ESC[{top};{bottom}r`) tells the *terminal* to confine
scrolling to a range of rows. Everything written afterwards - by this process
or by any child that inherits the console - scrolls inside that range and
leaves the rows above untouched. So the header survives without intercepting
anyone's output: the child writes to the real stdout exactly as it always did.

That property is the whole reason for this approach. The obvious alternative,
piping the child's stdout so its lines can be counted, would break step 1: it
asks for an OTP through `input()`, whose prompt carries no newline, so reading
its output line by line withholds the prompt until the user presses the Enter
they cannot know is wanted.

Progress comes from watching the filesystem instead (see the audit's
`*_progress` functions) - files appearing on disk, not lines parsed from a
stream.

When it does nothing
--------------------
Silently degrades to a plain `print` of the header whenever the split cannot
be honoured: output redirected to a file or a pipe, a terminal that will not
enable VT sequences, or one too short to spare the rows. A progress bar is a
convenience; losing the actual output to a formatting failure is not an
acceptable price for it.
"""

import os
import shutil
import sys
import threading
import time

CSI = "\x1b["
SAVE_CURSOR = "\x1b7"
RESTORE_CURSOR = "\x1b8"

BAR_FILLED = "█"  # full block
BAR_EMPTY = "░"   # light shade
BAR_WIDTH = 40


def enable_vt(stream):
    """Turn on VT escape processing, and report whether the stream can take it.

    Windows consoles honour escape sequences only once the mode bit is set;
    Windows Terminal usually sets it already, but conhost does not, and a
    plain `print` of an unhandled sequence puts literal garbage on screen.
    """
    if not hasattr(stream, "isatty") or not stream.isatty():
        return False
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        enable_virtual_terminal_processing = 0x0004
        return bool(kernel32.SetConsoleMode(
            handle, mode.value | enable_virtual_terminal_processing))
    except Exception:
        return False


def format_duration(seconds):
    seconds = int(seconds)
    if seconds >= 3600:
        return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60}:{seconds % 60:02d}"


def render_bar(done, total, width):
    """A bar of `width` cells, or an indeterminate one when total is unknown.

    An unknown total draws as empty rather than as a guess. The steps that
    cannot count themselves are the short ones; inventing a fraction for them
    would make the two steps that do know look equally untrustworthy.
    """
    if not total or total <= 0:
        return BAR_EMPTY * width
    filled = int(width * min(done, total) / total)
    return BAR_FILLED * filled + BAR_EMPTY * (width - filled)


def progress_line(done, total, unit, started, width):
    """`[####....]  47%   57/120 companies   12:04 elapsed   ~13:20 left`"""
    elapsed = time.monotonic() - started
    parts = []
    if total:
        pct = min(done, total) / total
        parts.append(f"{pct * 100:3.0f}%")
        parts.append(f"{done}/{total} {unit}")
    else:
        parts.append(f"{done} {unit}" if done else "starting")
    parts.append(f"{format_duration(elapsed)} elapsed")

    # Extrapolating from one finished item is noise, so no estimate until a
    # few are in - an ETA that swings by an hour between repaints is worse
    # than none at all.
    if total and done >= 3 and done < total:
        remaining = elapsed / done * (total - done)
        parts.append(f"~{format_duration(remaining)} left")

    # Fixed width, not "whatever the text leaves over": the suffix grows as the
    # count climbs and again when the estimate appears, and a bar that changes
    # length on every repaint reads as a glitch rather than as progress.
    bar_width = max(10, min(BAR_WIDTH, width - 20))
    return f"[{render_bar(done, total, bar_width)}]  {'   '.join(parts)}"[:width]


class Header:
    """A pinned block of `height` rows at the top of the terminal.

    Used as a context manager; the scroll region is a terminal-wide setting
    and has to be released even when the body raises, or every later command
    in that shell keeps scrolling inside a window it knows nothing about.
    """

    def __init__(self, height, stream=None):
        self.height = height
        self.stream = stream or sys.stdout
        self.lines = [""] * height
        self.active = False
        self._lock = threading.Lock()

    def __enter__(self):
        rows = shutil.get_terminal_size().lines
        # Below about this, pinning the header leaves too little room for the
        # output to be readable, and the point was to watch the output.
        if not enable_vt(self.stream) or rows < self.height + 6:
            return self

        # Scroll the header's rows into existence first: the region below
        # starts at the cursor's current screen position, so without this the
        # header would be painted over whatever was already on those rows.
        self.stream.write("\n" * self.height)
        self.stream.write(f"{CSI}{self.height + 1};{rows}r")  # DECSTBM
        self.stream.write(f"{CSI}{rows};1H")                  # into the region
        self.stream.flush()
        self.active = True
        return self

    def __exit__(self, *exc):
        if self.active:
            rows = shutil.get_terminal_size().lines
            with self._lock:
                self.stream.write(f"{CSI}r")        # full-screen scrolling again
                self.stream.write(f"{CSI}{rows};1H")
                self.stream.flush()
            self.active = False
        return False

    def set(self, lines):
        """Replace the header's contents and repaint it."""
        self.lines = list(lines)[:self.height]
        self.lines += [""] * (self.height - len(self.lines))
        self.paint()

    def paint(self):
        if not self.active:
            return
        width = shutil.get_terminal_size().columns
        # One write, because a child process is writing to this same console
        # concurrently. Between the cursor save and its restore the cursor is
        # parked in the header, and anything that lands there is painted over
        # a moment later; a single write keeps that window as short as it can be.
        out = [SAVE_CURSOR]
        for index, line in enumerate(self.lines):
            out.append(f"{CSI}{index + 1};1H{CSI}2K{line[:width]}")
        out.append(RESTORE_CURSOR)
        with self._lock:
            self.stream.write("".join(out))
            self.stream.flush()

    def print_fallback(self, lines):
        """Print the header inline, for when the split isn't available."""
        if not self.active:
            for line in lines:
                print(line)


class ProgressWatcher:
    """Repaints a header on a timer while a step runs.

    Polls rather than being told: what it watches is a child process's effect
    on the filesystem, and the child has no idea this is here.
    """

    def __init__(self, header, render, interval=1.0):
        self.header = header
        self.render = render
        self.interval = interval
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        self.header.set(self.render())
        if self.header.active:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        return self

    def _loop(self):
        while not self._stop.wait(self.interval):
            try:
                self.header.set(self.render())
            except Exception:
                # A repaint failing is never worth killing the audit over; the
                # step itself is the thing that matters and it is still running.
                pass

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2 * self.interval)
        return False
