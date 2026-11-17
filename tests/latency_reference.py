"""Independent truth table for Linux trace_print_lat_fmt's five columns."""

DEPTH_BYTES = tuple(range(16)) + tuple(depth << 4 for depth in range(1, 16)) + (0x42, 0xa5, 0xff)
RESCHED_CHARACTERS = {
    (False, False, False): '.',
    (True, False, False): 'n',
    (False, False, True): 'p',
    (True, False, True): 'N',
    (False, True, False): 'l',
    (False, True, True): 'L',
    (True, True, False): 'b',
    (True, True, True): 'B',
}


def linux_latency(flags, preempt, lazy=True):
    irq = {(False, False): '.', (True, False): 'd', (False, True): 'b', (True, True): 'D'}[
        bool(flags & 0x01), bool(flags & 0x80)]
    if not lazy and flags & 0x02 and irq == '.':
        irq = 'X'
    resched = RESCHED_CHARACTERS[bool(flags & 0x04), bool(lazy and flags & 0x02), bool(flags & 0x20)]
    if flags & 0x40:
        context = 'Z' if flags & 0x08 else 'z'
    else:
        context = {(False, False): '.', (True, False): 'h', (False, True): 's', (True, True): 'H'}[
            bool(flags & 0x08), bool(flags & 0x10)]
    return irq + resched + context + ''.join(format(depth, 'x') if depth else '.'
                                            for depth in (preempt & 15, preempt >> 4))
