#!/usr/bin/env python3
"""Re-time whisper SRT cues into subtitle-sized cues.

Whisper emits up to 30s / 300+ char segments. Split them into readable cues and
place each on the audio's *speech* timeline (silence skipped), so text doesn't
drift across pauses.

    srtfix.py in.srt silences.txt out.srt

silences.txt = raw `ffmpeg -af silencedetect` stderr.
"""
import re, sys

MAXCHARS = 84    # 2 lines x 42
LINEW    = 42
MIN_DUR  = 1.0
MAX_DUR  = 6.0
GAP      = 0.04  # keep cues from touching
# The speaker pauses mid-sentence, so a cue timed to its speech alone vanishes
# while he is still mid-thought. Hold each line until the next one needs the
# screen. Start times stay pinned to speech -- only ends are stretched.
HOLD     = True
HOLD_MAX = 0.0   # 0 = hold indefinitely; set e.g. 30.0 to cap long holds


def parse_srt(path):
    cues = []
    for b in open(path, encoding="utf-8").read().strip().split("\n\n"):
        L = b.strip().split("\n")
        m = re.search(r"(\d+):(\d+):(\d+)[,.](\d+) --> (\d+):(\d+):(\d+)[,.](\d+)", b)
        if not m or len(L) < 2:
            continue
        a = int(m[1])*3600 + int(m[2])*60 + int(m[3]) + int(m[4])/1000
        z = int(m[5])*3600 + int(m[6])*60 + int(m[7]) + int(m[8])/1000
        txt = " ".join(x.strip() for x in L[2:]).strip()
        if txt:
            cues.append((a, z, txt))
    return cues


def parse_silences(path):
    """-> sorted [(start,end)] of silent spans."""
    starts, sil = [], []
    for line in open(path, encoding="utf-8", errors="ignore"):
        s = re.search(r"silence_start:\s*([\d.]+)", line)
        e = re.search(r"silence_end:\s*([\d.]+)", line)
        if s:
            starts.append(float(s[1]))
        elif e and starts:
            sil.append((starts.pop(), float(e[1])))
    return sorted(sil)


def speech_spans(a, z, sil):
    """Speech sub-intervals of [a,z] (complement of silences)."""
    out, t = [], a
    for s0, s1 in sil:
        if s1 <= a or s0 >= z:
            continue
        s0, s1 = max(s0, a), min(s1, z)
        if s0 > t:
            out.append((t, s0))
        t = max(t, s1)
    if t < z:
        out.append((t, z))
    return out or [(a, z)]


def chunk(text, maxchars=MAXCHARS):
    """Greedy word packing; prefer breaking after sentence punctuation."""
    words, out, cur = text.split(), [], ""
    for w in words:
        cand = (cur + " " + w).strip()
        if cur and len(cand) > maxchars:
            out.append(cur)
            cur = w
        else:
            cur = cand
            # opportunistic break at a sentence end that is already long enough
            if re.search(r"[.!?]$", w) and len(cur) >= maxchars * 0.6:
                out.append(cur)
                cur = ""
    if cur:
        out.append(cur)
    return out


def wrap(s, w=LINEW):
    if len(s) <= w:
        return s
    words, lines, cur = s.split(), [], ""
    for x in words:
        c = (cur + " " + x).strip()
        if cur and len(c) > w:
            lines.append(cur); cur = x
        else:
            cur = c
    if cur:
        lines.append(cur)
    return "\n".join(lines[:2]) if len(lines) <= 2 else lines[0] + "\n" + " ".join(lines[1:])


def at_end(spans, offset):
    """Wall-clock at `offset` into the speech timeline; may land on a span end."""
    for a, z in spans:
        d = z - a
        if offset <= d:
            return a + offset
        offset -= d
    return spans[-1][1]


def at_start(spans, offset):
    """Same, but a boundary hit snaps forward to the next speech span, so a cue
    never begins inside a pause."""
    for a, z in spans:
        d = z - a
        if offset < d:
            return a + offset
        offset -= d
    return spans[-1][1]


def retime(cues, sil):
    out = []
    for a, z, txt in cues:
        parts = chunk(txt)
        spans = speech_spans(a, z, sil)
        total = sum(y - x for x, y in spans) or (z - a)
        nchars = sum(len(p) for p in parts) or 1
        off = 0.0
        for p in parts:
            share = total * len(p) / nchars
            s, e = at_start(spans, off), at_end(spans, off + share)
            off += share
            if e <= s:
                e = s + MIN_DUR
            if e - s < MIN_DUR:
                e = max(e, min(s + MIN_DUR, z))
            if e - s > MAX_DUR:
                e = s + MAX_DUR
            out.append([s, e, p])
    out.sort(key=lambda c: c[0])
    for i in range(len(out) - 1):
        nxt = out[i+1][0] - GAP
        if HOLD:
            end = nxt if not HOLD_MAX else min(nxt, out[i][0] + HOLD_MAX)
            out[i][1] = max(out[i][1], out[i][0] + 0.3, end)
        if out[i][1] > nxt:                            # never overlap the next cue
            out[i][1] = max(out[i][0] + 0.3, nxt)
    return out


# Whisper was trained on scraped video audio and, on stretches it cannot decode,
# emits canned filler instead. These never occur in an economics lecture, so any
# hit means real speech was REPLACED by invented text -- loud failure, not a warning
# to bury. Fix the audio/flags and re-run; do not just delete the cue.
HALLUCINATION = re.compile(
    r"thank(s)? (you )?for watching|don't forget to subscribe|subscribe to my channel|"
    r"see you (in|at) the next video|like and subscribe|hit the bell|"
    r"amara\.org|subtitles by|transcribed by", re.I)


def check_hallucinations(cues):
    hits = [(a, t) for a, z, t in cues if HALLUCINATION.search(t)]
    for a, t in hits:
        print(f"  !! HALLUCINATION at {int(a)//60:02d}:{int(a)%60:02d} -> {t[:70]!r}")
    if hits:
        print(f"  !! {len(hits)} hallucinated cue(s): real speech was replaced. "
              f"Re-decode with -sns before using this transcript.")
    return hits


def write_raw(cues, path):
    """Write cues verbatim (no re-wrapping) -- for intermediate whisper SRTs."""
    with open(path, "w", encoding="utf-8") as f:
        for i, (a, z, t) in enumerate(cues, 1):
            f.write(f"{i}\n{ts(a)} --> {ts(z)}\n{t}\n\n")


def splice(base, win, offset, s, e, out):
    """Replace base cues overlapping [s,e] with win's cues (shifted by offset).

    A hallucination is an artifact of where whisper's 30 s windows fall, so
    re-decoding the same audio on a different boundary recovers the real speech.
    """
    add = [(a + offset, z + offset, t) for a, z, t in parse_srt(win)
           if a + offset < e and z + offset > s]
    if not add:
        return 0
    keep = [c for c in parse_srt(base) if not (c[0] < e and c[1] > s)]
    merged = sorted(keep + add, key=lambda c: c[0])
    # Inserted cues spill a little past the flagged span, so the base cue just
    # outside it can repeat text the window already carries -- a visible stutter
    # at each seam. Widening the cleared range instead would drop base cues the
    # window does not cover, losing speech, so de-duplicate here and keep the
    # fuller wording of the two.
    out_cues = []
    for c in merged:
        if out_cues:
            prev = out_cues[-1][2].strip()
            cur = c[2].strip()
            if cur and prev and (cur in prev or prev in cur):
                if len(cur) > len(prev):
                    out_cues[-1] = c
                continue
        out_cues.append(c)
    write_raw(out_cues, out)
    return len(add)


def ts(t):
    h, r = divmod(max(t, 0), 3600); m, s = divmod(r, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s - int(s))*1000)):03d}"


def write(cues, path):
    with open(path, "w", encoding="utf-8") as f:
        for i, (a, z, t) in enumerate(cues, 1):
            f.write(f"{i}\n{ts(a)} --> {ts(z)}\n{wrap(t)}\n\n")


def demo():
    sil = [(2.0, 8.0)]                     # 6s pause inside the cue
    cues = [(0.0, 12.0, "One two three. " * 12)]
    out = retime(cues, sil)
    assert len(out) > 1, "long cue must split"
    assert all(len(t) <= MAXCHARS for _, _, t in out), "cue text too wide"
    for i in range(len(out) - 1):
        assert out[i][1] <= out[i+1][0], "cues overlap"
    # no cue may sit entirely inside the silent gap
    assert not any(a >= 2.0 and z <= 8.0 for a, z, _ in out), "cue placed in silence"
    # a line must stay up until the next one needs the screen, so the speaker's
    # mid-sentence pauses never leave a blank screen
    for i in range(len(out) - 1):
        assert out[i][1] >= out[i+1][0] - GAP - 1e-6, "gap left between cues"
    # holding must not drag start times off the speech they belong to
    plain = retime(cues, sil)
    assert [round(a, 6) for a, _, _ in out] == [round(a, 6) for a, _, _ in plain], \
        "hold changed a start time"
    assert speech_spans(0, 12, sil) == [(0.0, 2.0), (8.0, 12.0)]
    assert at_end([(0, 2), (8, 12)], 3.0) == 9.0, "speech-time mapping"
    assert at_end([(0, 2), (8, 12)], 2.0) == 2.0, "end may rest on a boundary"
    assert at_start([(0, 2), (8, 12)], 2.0) == 8.0, "start skips the pause"

    # splice: a re-decoded window replaces the bad span, and its spill past the
    # span must not leave the neighbouring base cue behind as a duplicate
    import tempfile, os
    d = tempfile.mkdtemp()
    b_, w_, o_ = (os.path.join(d, n) for n in ("b.srt", "w.srt", "o.srt"))
    # window is padded well past the span (as in the real pipeline) and its last
    # cue spills past `e`, repeating text the base also has just outside it
    # the recovered cue spills past `e` and repeats what the next base cue says
    # -- exactly the stutter seen at 36:05 and 61:38 in a real lecture
    write_raw([(0, 10, "good before"), (10, 20, "BAD FILLER"), (21, 30, "the tail")], b_)
    write_raw([(0, 12, "real speech and then the tail")], w_)   # window starts at 10
    splice(b_, w_, 10.0, 10.0, 20.0, o_)
    got = [t for _, _, t in parse_srt(o_)]
    assert "BAD FILLER" not in got, "hallucinated cue survived the splice"
    assert "good before" in got, "splice clobbered untouched audio"
    assert sum("the tail" in t for t in got) == 1, f"seam duplicated: {got}"
    assert "real speech and then the tail" in got, f"kept the poorer wording: {got}"
    print("srtfix self-check ok")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--demo":
        demo(); sys.exit()
    if sys.argv[1] == "--find":                 # print "start end" per bad cue
        for a, z, t in parse_srt(sys.argv[2]):
            if HALLUCINATION.search(t):
                print(f"{a:.3f} {z:.3f}")
        sys.exit()
    if sys.argv[1] == "--splice":               # base win offset s e out
        b, w, off, s, e, out = sys.argv[2:8]
        n = splice(b, w, float(off), float(s), float(e), out)
        print(f"spliced {n} cue(s) over {float(s):.1f}-{float(e):.1f}s")
        sys.exit()
    argv = sys.argv[1:]
    shift = 0.0
    if "--shift" in argv:                       # put cues back on the ORIGINAL
        i = argv.index("--shift")               # recording's timeline
        shift = float(argv[i + 1]); del argv[i:i + 2]
    src, silf, dst = argv[0], argv[1], argv[2]
    argv = argv[3:]
    cues = parse_srt(src)
    if argv:                                    # optional glossary: regex<TAB>replacement
        rules, hits = [], 0
        for line in open(argv[0], encoding="utf-8"):
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#") or "\t" not in line:
                continue
            pat, rep = line.split("\t", 1)
            rules.append((re.compile(pat), rep))
        fixed = []
        for a, z, t in cues:
            for pat, rep in rules:
                t, n = pat.subn(rep, t)
                hits += n
            fixed.append((a, z, t))
        cues = fixed
        print(f"glossary: {hits} substitutions from {len(rules)} rules")
    bad = check_hallucinations(cues)
    c = retime(cues, parse_silences(silf))
    if shift:
        c = [[a + shift, z + shift, t] for a, z, t in c]
    write(c, dst)
    print(f"{dst}: {len(c)} cues" + ("  [HALLUCINATIONS PRESENT]" if bad else ""))
