"""Inline frames of call sites, from (fast)faddr2line output.

For a return address `caller+off/len`, faddr2line prints the source position and,
when the call site sits in inlined code, the chain of inlined functions:

    arch_stack_walk+0x2cf/0x490:
    kunwind_next_frame_record at arch/arm64/kernel/stacktrace.c:227
     (inlined by) kunwind_next at arch/arm64/kernel/stacktrace.c:266
     (inlined by) arch_stack_walk at arch/arm64/kernel/stacktrace.c:390

(with --list, source context lines follow each position). The first line is the
innermost frame, the last one the real (traced) function that contains the code.

`parse_chain()` turns this into frames ordered outer -> inner:
    [[function, file, line], ...]   chain[0] = containing function, chain[1:] = inlined frames
where `line` is the position inside that frame: for chain[k] it is the line that
"calls" chain[k+1] (or, for the last frame, the line of the traced call itself).
"""
import re

RE_POS = re.compile(r'^\s*(?:\(inlined by\)\s+)?(?:\(inlined\)\s+)?([A-Za-z_$][\w.$]*) at (\S+?):(\d+)\b')
RE_CODE = re.compile(r'^\s*>?\d+<?\s')
RE_HEAD = re.compile(r'^\S+\+0x[0-9a-fA-F]+/0x[0-9a-fA-F]+(?: \[[\w-]+\])?:$')


def parse_chain(text, rel=None):
    """Parse one faddr2line result (str) -> frames outer->inner, or [] if nothing found.

    rel: optional callable mapping a file path to the displayed (repository-relative) path.
    """
    if not text or not isinstance(text, str):
        return []
    frames = []
    for line in text.split('\n'):
        st = line.strip()
        if not st or RE_CODE.match(line):
            continue
        if RE_HEAD.match(st):
            if frames:
                break               # a second candidate (duplicate symbol names): keep the first
            continue
        m = RE_POS.match(line)
        if not m:
            continue
        inlined = st.startswith('(inlined by)')
        if frames and not inlined:
            break                   # a new, unrelated position
        path = m.group(2)
        if rel is not None:
            try:
                path = rel(path) or path
            except Exception:
                pass
        frames.append([m.group(1), path, int(m.group(3))])
    frames.reverse()
    return frames


def timeline_inline_map(strings, caller_col, lookup):
    """Map caller string index -> chain (only for call sites with inlined frames).

    strings: tlData string table; caller_col: list of string indices (or None)
    lookup: callable(caller_string) -> faddr2line text or None
    """
    out = {}
    if not caller_col:
        return out
    seen = set()
    for idx in caller_col:
        if idx is None or idx < 0 or idx in seen:
            continue
        seen.add(idx)
        chain = parse_chain(lookup(strings[idx]))
        if len(chain) > 1:
            out[str(idx)] = chain
    return out
