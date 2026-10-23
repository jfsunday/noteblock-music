"""Eingaben laden: MIDI direkt, Audio (mp3/wav/ogg/flac…) per Transkription."""
from dataclasses import dataclass, field
from pathlib import Path

MIDI_EXT = {".mid", ".midi", ".kar"}


@dataclass
class RawNote:
    beat: float          # Startzeit in Schlägen (Viertel)
    pitch: int           # MIDI-Ton
    velocity: int
    track: str
    program: int
    drum: bool


@dataclass
class Song:
    title: str
    bpm: float
    beats_per_bar: float
    notes: list = field(default_factory=list)
    source: str = "midi"


def load(path: str, **audio_opts) -> Song:
    p = Path(path)
    if p.suffix.lower() in MIDI_EXT:
        return load_midi(p)
    return load_audio(p, **audio_opts)


def load_midi(path, data: bytes = None, title: str = None) -> Song:
    import io
    import mido
    mid = mido.MidiFile(file=io.BytesIO(data)) if data is not None else mido.MidiFile(str(path))
    tpb = mid.ticks_per_beat
    bpm, beats_per_bar = None, 4.0
    notes = []
    for ti, track in enumerate(mid.tracks):
        name = track.name or f"Spur {ti}"
        programs = {}
        t = 0
        for msg in track:
            t += msg.time
            if msg.type == "set_tempo" and bpm is None:
                bpm = mido.tempo2bpm(msg.tempo)
            elif msg.type == "time_signature" and t == 0:
                beats_per_bar = msg.numerator * 4 / msg.denominator
            elif msg.type == "program_change":
                programs[msg.channel] = msg.program
            elif msg.type == "note_on" and msg.velocity > 0:
                notes.append(RawNote(t / tpb, msg.note, msg.velocity, name,
                                     programs.get(msg.channel, 0), msg.channel == 9))
    notes.sort(key=lambda n: n.beat)
    return Song(title or Path(path).stem, bpm or 120.0, beats_per_bar, notes, "midi")


def load_audio(path: Path, drums: bool = True, onset_threshold: float = 0.5,
               frame_threshold: float = 0.3, min_note_ms: float = 80) -> Song:
    """Audio -> Noten. Melodie/Harmonie per Spotify basic-pitch (ONNX),
    Schlagzeug per Onset-Erkennung auf dem perkussiven Anteil (librosa)."""
    import warnings
    warnings.filterwarnings("ignore")
    import librosa
    import numpy as np
    from basic_pitch import ICASSP_2022_MODEL_PATH
    from basic_pitch.inference import predict

    y, sr = librosa.load(str(path), sr=22050, mono=True)
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    bpm = float(np.atleast_1d(tempo)[0]) or 120.0
    spb = 60.0 / bpm

    _, _, events = predict(str(path), ICASSP_2022_MODEL_PATH,
                           onset_threshold=onset_threshold,
                           frame_threshold=frame_threshold,
                           minimum_note_length=min_note_ms)
    mel = [(start, int(pitch), float(amp)) for start, end, pitch, amp, _ in events]
    drum_hits = []
    if drums:
        _, y_perc = librosa.effects.hpss(y)
        onsets = librosa.onset.onset_detect(y=y_perc, sr=sr, units="time", backtrack=False)
        strength = np.abs(y_perc)
        for t in onsets:
            a, b = int(t * sr), int(min(len(y_perc), t * sr + 0.05 * sr))
            seg = y_perc[a:b]
            if len(seg) < 64:
                continue
            centroid = float(librosa.feature.spectral_centroid(y=seg, sr=sr).mean())
            vel = int(min(127, 40 + 600 * float(strength[a:b].mean())))
            gm = 36 if centroid < 1500 else (38 if centroid < 4000 else 42)
            drum_hits.append((float(t), gm, vel))
    return song_from_audio(path.stem, bpm, mel, drum_hits)


def song_from_audio(title, bpm, melodic, drum_hits=()):
    """melodic: [(start_s, midi, amplitude 0..1)], drum_hits: [(t_s, gm_note, velocity)]"""
    spb = 60.0 / bpm
    notes = [RawNote(start / spb, int(pitch), int(min(127, amp * 127)),
                     "Bass (Audio)" if pitch < 48 else "Melodie (Audio)",
                     32 if pitch < 48 else 0, False) for start, pitch, amp in melodic]
    notes += [RawNote(t / spb, int(gm), int(vel), "Drums (Audio)", 0, True) for t, gm, vel in drum_hits]
    notes.sort(key=lambda n: n.beat)
    return Song(title, bpm, 4.0, notes, "audio")
