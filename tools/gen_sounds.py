"""
Procedural audio generator for Modern Warfare 4X.

Generates WAV files under ``assets/sfx/`` and ``assets/music/`` using only
Python stdlib (``wave``, ``array``, ``math``, ``random``).  No numpy or
external audio library required.

Run from the project root::

    python tools/gen_sounds.py

Outputs
-------
assets/sfx/move.wav        — soft whoosh (0.18 s)
assets/sfx/attack.wav      — percussive boom (0.30 s)
assets/sfx/capture.wav     — ascending chime arpeggio (0.55 s)
assets/sfx/build.wav       — mechanical clank (0.28 s)
assets/sfx/end_turn.wav    — clean click + short ping (0.22 s)
assets/sfx/win.wav         — ascending major fanfare (1.40 s)
assets/sfx/lose.wav        — descending minor resolution (1.10 s)
assets/sfx/levelup.wav     — bright two-tone rank-up chime (0.50 s)

assets/music/NATO.wav      — crisp march ostinato loop (6.0 s)
assets/music/BRICS.wav     — heavy industrial drone loop (6.0 s)
assets/music/GUERILLA.wav  — sparse, syncopated guerrilla loop (6.0 s)

The files are functional placeholder audio.  To swap in polished assets,
replace individual WAVs at the same paths — no code changes needed.
"""
from __future__ import annotations

import array
import math
import os
import random
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SFX_DIR   = ROOT / "assets" / "sfx"
MUSIC_DIR = ROOT / "assets" / "music"
SFX_DIR.mkdir(parents=True, exist_ok=True)
MUSIC_DIR.mkdir(parents=True, exist_ok=True)

RATE   = 44100
INT16_MAX = 32767


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _clamp(v: float) -> int:
    return max(-INT16_MAX, min(INT16_MAX, int(v)))


def write_wav(path: Path, samples: list[float], channels: int = 1) -> None:
    """Write 16-bit signed PCM WAV at RATE Hz."""
    data = array.array("h", (_clamp(s) for s in samples))
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(RATE)
        wf.writeframes(data.tobytes())
    print(f"  {path.relative_to(ROOT)}")


def sine(freq: float, dur: float, amp: float = 0.5, phase: float = 0.0) -> list[float]:
    n = int(dur * RATE)
    return [amp * INT16_MAX * math.sin(2 * math.pi * freq * i / RATE + phase)
            for i in range(n)]


def noise(dur: float, amp: float = 0.35) -> list[float]:
    n = int(dur * RATE)
    return [amp * INT16_MAX * (random.random() * 2 - 1) for _ in range(n)]


def envelope(samples: list[float],
             attack: float = 0.01,
             decay:  float = 0.05,
             sustain_level: float = 0.7,
             release: float = 0.10) -> list[float]:
    n = len(samples)
    a_end = int(attack  * RATE)
    d_end = int((attack + decay) * RATE)
    r_start = n - int(release * RATE)
    out: list[float] = []
    for i, s in enumerate(samples):
        if i < a_end:
            g = i / max(1, a_end)
        elif i < d_end:
            t = (i - a_end) / max(1, d_end - a_end)
            g = 1.0 - t * (1.0 - sustain_level)
        elif i < r_start:
            g = sustain_level
        else:
            t = (i - r_start) / max(1, n - r_start)
            g = sustain_level * (1.0 - t)
        out.append(s * g)
    return out


def fade_in(samples: list[float], dur: float) -> list[float]:
    n_fade = int(dur * RATE)
    return [s * (i / n_fade if i < n_fade else 1.0)
            for i, s in enumerate(samples)]


def fade_out(samples: list[float], dur: float) -> list[float]:
    n = len(samples)
    n_fade = int(dur * RATE)
    start  = n - n_fade
    return [s * (1.0 - (i - start) / n_fade if i >= start else 1.0)
            for i, s in enumerate(samples)]


def mix_add(*tracks: list[float]) -> list[float]:
    """Sum tracks to the length of the longest; normalise if clipping."""
    n = max(len(t) for t in tracks)
    out = [0.0] * n
    for t in tracks:
        for i, s in enumerate(t):
            out[i] += s
    peak = max(abs(v) for v in out) if out else 1.0
    if peak > INT16_MAX:
        scale = INT16_MAX / peak
        out = [v * scale for v in out]
    return out


def pad_to(samples: list[float], target_n: int, value: float = 0.0) -> list[float]:
    n = len(samples)
    if n >= target_n:
        return samples[:target_n]
    return samples + [value] * (target_n - n)


def note_hz(midi: int) -> float:
    """MIDI note number → Hz (A4 = 69 = 440 Hz)."""
    return 440.0 * (2 ** ((midi - 69) / 12.0))


# ---------------------------------------------------------------------------
# SFX generators
# ---------------------------------------------------------------------------

def gen_move() -> list[float]:
    """Soft whoosh: short band of filtered noise rising in pitch."""
    dur   = 0.18
    base  = noise(dur, amp=0.40)
    # Layer two swept sines for 'swoosh' texture
    sweep = [
        0.20 * INT16_MAX * math.sin(2 * math.pi * (300 + 1200 * i / (dur * RATE)) * i / RATE)
        for i in range(int(dur * RATE))
    ]
    combined = [b + s for b, s in zip(base, sweep)]
    return envelope(combined, attack=0.02, decay=0.05, sustain_level=0.4, release=0.08)


def gen_attack() -> list[float]:
    """Percussive boom: sharp noise burst + low sine thud."""
    dur   = 0.30
    boom  = envelope(noise(dur, amp=0.55), attack=0.002, decay=0.02, sustain_level=0.15, release=0.20)
    thud  = envelope(sine(70, dur, amp=0.60),  attack=0.002, decay=0.04, sustain_level=0.10, release=0.18)
    crack = envelope(sine(200, 0.06, amp=0.30), attack=0.001, decay=0.01, sustain_level=0.05, release=0.03)
    crack = pad_to(crack, int(dur * RATE))
    return mix_add(boom, thud, crack)


def gen_capture() -> list[float]:
    """Ascending chime arpeggio: three notes of a major chord."""
    notes = [60, 64, 67]  # C4, E4, G4
    note_dur = 0.16
    gap      = 0.03
    result: list[float] = []
    for n in notes:
        snd = envelope(
            sine(note_hz(n), note_dur, amp=0.55),
            attack=0.01, decay=0.04, sustain_level=0.60, release=0.06
        )
        result.extend(snd)
        result.extend([0.0] * int(gap * RATE))
    return result


def gen_build() -> list[float]:
    """Metallic clank: short harmonic burst."""
    dur  = 0.28
    fund = 220.0
    harmonics = [
        (1.00, 0.40),
        (2.76, 0.25),
        (5.40, 0.15),
        (8.93, 0.08),
    ]
    parts = [
        envelope(sine(fund * ratio, dur, amp=amp),
                 attack=0.003, decay=0.05, sustain_level=0.20, release=0.12)
        for ratio, amp in harmonics
    ]
    clank = mix_add(*parts)
    texture = envelope(noise(dur, amp=0.15), attack=0.002, decay=0.02,
                       sustain_level=0.05, release=0.10)
    return mix_add(clank, texture)


def gen_end_turn() -> list[float]:
    """Clean click + short ping."""
    click = envelope(noise(0.04, amp=0.50),
                     attack=0.001, decay=0.008, sustain_level=0.05, release=0.02)
    ping  = envelope(sine(880, 0.20, amp=0.35),
                     attack=0.003, decay=0.05, sustain_level=0.40, release=0.10)
    gap   = [0.0] * int(0.02 * RATE)
    result = list(click) + gap + list(ping)
    return result


def gen_win() -> list[float]:
    """Ascending major fanfare: C4-E4-G4-C5 held notes."""
    melody = [
        (60, 0.20),  # C4
        (64, 0.20),  # E4
        (67, 0.20),  # G4
        (72, 0.50),  # C5 (held)
    ]
    gap_samples = [0.0] * int(0.04 * RATE)
    result: list[float] = []
    for midi, dur in melody:
        note = envelope(
            sine(note_hz(midi), dur, amp=0.60),
            attack=0.01, decay=0.06, sustain_level=0.65, release=0.08
        )
        # Add a harmony a third above
        harm = envelope(
            sine(note_hz(midi + 4), dur, amp=0.25),
            attack=0.01, decay=0.06, sustain_level=0.65, release=0.08
        )
        combined = [a + b for a, b in zip(note, harm)]
        result.extend(combined)
        result.extend(gap_samples)
    return fade_out(result, 0.15)


def gen_levelup() -> list[float]:
    """Bright two-tone rank-up chime: quick rising fifth then shimmering hold.

    Inspired by the capture arpeggio but shorter and more triumphant.
    C5 -> G5 glide, then held G5 + E5 harmony with a short shimmer tail.
    Total duration ~0.50 s.
    """
    # Rising arpeggio: C5 (72) -> E5 (76) -> G5 (79)
    notes = [72, 76, 79]
    note_dur = 0.10
    gap      = 0.015
    result: list[float] = []
    for n in notes:
        body = envelope(
            sine(note_hz(n), note_dur, amp=0.55),
            attack=0.005, decay=0.03, sustain_level=0.65, release=0.04,
        )
        result.extend(body)
        result.extend([0.0] * int(gap * RATE))

    # Held G5 + E5 harmony (the triumphant ring)
    hold_dur = 0.20
    held = mix_add(
        envelope(sine(note_hz(79), hold_dur, amp=0.50),
                 attack=0.01, decay=0.04, sustain_level=0.60, release=0.08),
        envelope(sine(note_hz(76), hold_dur, amp=0.25),
                 attack=0.01, decay=0.04, sustain_level=0.55, release=0.08),
        # Shimmer: add a very faint octave above
        envelope(sine(note_hz(91), hold_dur, amp=0.10),
                 attack=0.02, decay=0.05, sustain_level=0.30, release=0.06),
    )
    result.extend(held)
    return fade_out(result, 0.08)


def gen_lose() -> list[float]:
    """Descending minor resolution."""
    melody = [
        (60, 0.22),  # C4
        (58, 0.22),  # Bb3
        (56, 0.22),  # Ab3
        (55, 0.44),  # G3 (held, minor third + tritone)
    ]
    gap_samples = [0.0] * int(0.04 * RATE)
    result: list[float] = []
    for midi, dur in melody:
        note = envelope(
            sine(note_hz(midi), dur, amp=0.55),
            attack=0.01, decay=0.08, sustain_level=0.55, release=0.10
        )
        result.extend(note)
        result.extend(gap_samples)
    return fade_out(result, 0.18)


# ---------------------------------------------------------------------------
# Music loop generators (6-second loops)
# ---------------------------------------------------------------------------

LOOP_DUR = 6.0
BPM      = 120
BEAT     = 60.0 / BPM        # 0.5 s


def _arpeggiate(chord_midi: list[int], pattern: list[int],
                note_dur: float, amp: float, loop_n: int) -> list[float]:
    """Lay down an arpeggio pattern repeating over loop_n beats."""
    total_n = int(LOOP_DUR * RATE)
    out     = [0.0] * total_n
    t       = 0.0
    beat_i  = 0
    n_beats = int(LOOP_DUR / BEAT)
    for b in range(n_beats):
        midi = chord_midi[pattern[b % len(pattern)] % len(chord_midi)]
        i0   = int(t * RATE)
        snd  = envelope(sine(note_hz(midi), note_dur, amp=amp),
                        attack=0.01, decay=0.05, sustain_level=0.55, release=0.08)
        for j, v in enumerate(snd):
            idx = i0 + j
            if idx < total_n:
                out[idx] += v
        t += BEAT
    return out


def gen_music_nato() -> list[float]:
    """NATO: crisp march ostinato in C major."""
    n = int(LOOP_DUR * RATE)
    # Bass line: root + fifth alternating each beat
    bass_notes = [48, 55, 48, 55, 48, 55, 48, 55, 48, 55, 48, 55]  # C3/G3
    bass = [0.0] * n
    t = 0.0
    for midi in bass_notes:
        i0  = int(t * RATE)
        snd = envelope(sine(note_hz(midi), BEAT * 0.8, amp=0.45),
                       attack=0.005, decay=0.06, sustain_level=0.35, release=0.12)
        for j, v in enumerate(snd):
            if i0 + j < n:
                bass[i0 + j] += v
        t += BEAT

    # Melody arpeggio: C major (C-E-G-C) — quarter-note pattern
    chord   = [60, 64, 67, 72]   # C4, E4, G4, C5
    pattern = [0, 1, 2, 3, 2, 1, 0, 1, 2, 3, 2, 1]
    melody  = _arpeggiate(chord, pattern, BEAT * 0.75, amp=0.30, loop_n=12)

    # Snare/kick texture (noise bursts on beats 2 and 4)
    snare = [0.0] * n
    for beat in [1, 3, 5, 7, 9, 11]:
        i0  = int(beat * BEAT * RATE)
        hit = envelope(noise(0.06, amp=0.25),
                       attack=0.001, decay=0.01, sustain_level=0.05, release=0.03)
        for j, v in enumerate(hit):
            if i0 + j < n:
                snare[i0 + j] += v

    result = mix_add(bass, melody, snare)
    return fade_in(fade_out(result, 0.20), 0.20)


def gen_music_brics() -> list[float]:
    """BRICS: heavy industrial drone in A minor."""
    n = int(LOOP_DUR * RATE)
    # Heavy low-end bass (A2 root + E3 fifth)
    bass_notes = [45, 52, 45, 52, 45, 52, 45, 52, 45, 52, 45, 52]  # A2/E3
    bass = [0.0] * n
    t = 0.0
    for midi in bass_notes:
        i0  = int(t * RATE)
        dur = BEAT * 0.9
        snd = envelope(sine(note_hz(midi), dur, amp=0.55),
                       attack=0.008, decay=0.08, sustain_level=0.40, release=0.15)
        # Add a detuned layer for industrial grit
        snd2 = envelope(sine(note_hz(midi) * 1.008, dur, amp=0.20),
                        attack=0.008, decay=0.08, sustain_level=0.40, release=0.15)
        for j, (v, v2) in enumerate(zip(snd, snd2)):
            if i0 + j < n:
                bass[i0 + j] += v + v2
        t += BEAT

    # Minor chord arpeggio: A-C-E (descending feel)
    chord   = [57, 60, 64]   # A3, C4, E4
    pattern = [2, 1, 0, 1, 2, 1, 0, 0, 2, 1, 0, 1]
    melody  = _arpeggiate(chord, pattern, BEAT * 0.65, amp=0.22, loop_n=12)

    # Heavier noise hits (every beat)
    perc = [0.0] * n
    for beat in range(12):
        i0  = int(beat * BEAT * RATE)
        amp = 0.30 if beat % 4 == 0 else 0.14
        hit = envelope(noise(0.08, amp=amp),
                       attack=0.001, decay=0.02, sustain_level=0.06, release=0.04)
        for j, v in enumerate(hit):
            if i0 + j < n:
                perc[i0 + j] += v

    result = mix_add(bass, melody, perc)
    return fade_in(fade_out(result, 0.20), 0.20)


def gen_music_guerilla() -> list[float]:
    """GUERILLA: sparse, syncopated in D minor (odd-beat accents)."""
    n = int(LOOP_DUR * RATE)
    # Sparse bass — hits on beats 1, 3.5, 5, 7, 9.5, 11
    bass_pattern = [0, 3.5, 5, 7, 9.5, 11]   # beat offsets (floats)
    bass_notes   = [50, 53, 57, 50, 53, 57]   # D3, F3, A3 rotating
    bass = [0.0] * n
    for off, midi in zip(bass_pattern, bass_notes):
        i0  = int(off * BEAT * RATE)
        snd = envelope(sine(note_hz(midi), BEAT * 0.60, amp=0.48),
                       attack=0.005, decay=0.07, sustain_level=0.30, release=0.12)
        for j, v in enumerate(snd):
            if i0 + j < n:
                bass[i0 + j] += v

    # Sparse high melody hits (dim-chord: D-F-Ab)
    mel_pattern = [1, 2.5, 4, 6, 7.5, 10]
    mel_notes   = [62, 65, 68, 62, 65, 68]  # D4, F4, Ab4
    melody = [0.0] * n
    for off, midi in zip(mel_pattern, mel_notes):
        i0  = int(off * BEAT * RATE)
        snd = envelope(sine(note_hz(midi), BEAT * 0.45, amp=0.25),
                       attack=0.008, decay=0.04, sustain_level=0.35, release=0.08)
        for j, v in enumerate(snd):
            if i0 + j < n:
                melody[i0 + j] += v

    # Syncopated noise — off-beat taps
    perc = [0.0] * n
    for off in [0.5, 1.5, 3.0, 4.5, 6.5, 8.0, 9.0, 11.0]:
        i0  = int(off * BEAT * RATE)
        amp = 0.22 if int(off) % 2 == 0 else 0.14
        hit = envelope(noise(0.05, amp=amp),
                       attack=0.001, decay=0.01, sustain_level=0.03, release=0.03)
        for j, v in enumerate(hit):
            if i0 + j < n:
                perc[i0 + j] += v

    result = mix_add(bass, melody, perc)
    return fade_in(fade_out(result, 0.20), 0.20)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    random.seed(42)   # deterministic output for reproducibility

    print("Generating SFX...")
    for name, gen in [
        ("move",     gen_move),
        ("attack",   gen_attack),
        ("capture",  gen_capture),
        ("build",    gen_build),
        ("end_turn", gen_end_turn),
        ("win",      gen_win),
        ("lose",     gen_lose),
        ("levelup",  gen_levelup),
    ]:
        write_wav(SFX_DIR / f"{name}.wav", gen())

    print("Generating music loops...")
    for faction, gen in [
        ("NATO",     gen_music_nato),
        ("BRICS",    gen_music_brics),
        ("GUERILLA", gen_music_guerilla),
    ]:
        write_wav(MUSIC_DIR / f"{faction}.wav", gen())

    total = 8 + 3
    print(f"Done. {total} audio files generated.")


if __name__ == "__main__":
    main()
