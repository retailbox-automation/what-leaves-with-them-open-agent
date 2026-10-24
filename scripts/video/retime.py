#!/usr/bin/env python3
"""Fit one part of a recorded terminal session (asciicast v2) into a shot length by shortening the waits only.
usage: python3 retime.py <in.cast> <out.cast> --target S [--from T0] [--to T1] [--hold H]
Output events between T0 and T1 keep their order and content; everything printed before T0 is shown at once at the
start (the screen as it was at T0). Every pause between two outputs is capped at L seconds, with L chosen so that the
part plus a final hold of H seconds lasts --target seconds. Prints L and the speed-up (real seconds / shown seconds)."""
import argparse
import json


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('inp'); ap.add_argument('out'); ap.add_argument('--target', type=float, required=True)
    ap.add_argument('--from', dest='t0', type=float, default=0.0); ap.add_argument('--to', dest='t1', type=float, default=1e9)
    ap.add_argument('--hold', type=float, default=2.0)
    a = ap.parse_args()
    lines = open(a.inp).read().splitlines()
    head, ev = json.loads(lines[0]), [json.loads(l) for l in lines[1:] if l.strip()]
    before = [e for e in ev if e[0] <= a.t0 and e[1] == 'o']
    part = [e for e in ev if a.t0 < e[0] <= a.t1 and e[1] == 'o']
    times = [a.t0] + [e[0] for e in part]
    gaps = [times[i + 1] - times[i] for i in range(len(times) - 1)]
    budget = a.target - a.hold
    lo, hi = 0.0, max(gaps + [budget])
    for _ in range(60):
        L = (lo + hi) / 2
        if sum(min(g, L) for g in gaps) > budget:
            hi = L
        else:
            lo = L
    L = lo
    out, t = [], 0.0
    if before:
        out.append([0.0, 'o', ''.join(e[2] for e in before)])
    for g, e in zip(gaps, part):
        t += min(g, L)
        out.append([round(t, 6), 'o', e[2]])
    shown = t + a.hold
    real = (times[-1] - a.t0) if part else 0.0
    with open(a.out, 'w') as f:
        f.write(json.dumps(head) + '\n')
        for e in out:
            f.write(json.dumps(e) + '\n')
    print(json.dumps({'cap_s': round(L, 3), 'real_s': round(real, 1), 'shown_s': round(shown, 2),
                      'speedup': round(real / max(t, 1e-6), 1) if part else None, 'events': len(part)}))


if __name__ == '__main__':
    main()
