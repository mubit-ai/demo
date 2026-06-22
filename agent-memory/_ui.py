"""Terminal styling helpers (respects NO_COLOR / non-TTY).

Legibility rule: dim() is reserved for true metadata only (pid, session id).
Everything the audience must read — answers, recalled memories, verdicts — is
rendered in normal or bright/bold so it survives a projector in a lit room.
"""
import os
import sys

_TTY = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(s, code):
    return f"\033[{code}m{s}\033[0m" if _TTY else s


def bold(s):
    return _c(s, "1")


def dim(s):
    return _c(s, "2")  # metadata ONLY


def green(s):
    return _c(s, "32")


def red(s):
    return _c(s, "31")


def yellow(s):
    return _c(s, "33")


def cyan(s):
    return _c(s, "96")  # bright cyan — load-bearing


def white(s):
    return _c(s, "97")  # bright white — the agent's answer


def banner(title, sub=""):
    line = "═" * 66
    print()
    print(cyan(line))
    print("  " + bold(title))
    if sub:
        print("  " + dim(sub))
    print(cyan(line))


def verdict_label(frac, found):
    from scenario import EXPECTED_KEYWORDS

    miss = [k for k in EXPECTED_KEYWORDS if k not in found]
    tag = (green if not miss else red)(bold(" PASS " if not miss else " FAIL "))
    detail = f"score={frac:.2f}  " + green("hit=%s" % found)
    if miss:
        detail += "  " + red("missing=%s" % miss)
    return f"[{tag}] {detail}"
