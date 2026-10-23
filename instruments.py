"""Minecraft-Notenblock-Instrumente (Java Edition) und MIDI-Zuordnung."""
from dataclasses import dataclass

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_name(midi: int) -> str:
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


@dataclass(frozen=True)
class Instrument:
    key: str
    name: str        # deutscher Anzeigename
    block: str       # Block unter dem Notenblock
    low: int         # MIDI-Ton bei 0 Klicks (F#), Umfang low..low+24
    percussion: bool = False

    @property
    def high(self) -> int:
        return self.low + 24


INSTRUMENTS = {i.key: i for i in [
    Instrument("harp", "Harfe/Klavier", "Erde (oder jeder andere Block)", 54),
    Instrument("bass", "Kontrabass", "Holzbretter (Holz)", 30),
    Instrument("didgeridoo", "Didgeridoo", "Kürbis", 30),
    Instrument("guitar", "Gitarre", "Wolle", 42),
    Instrument("flute", "Flöte", "Ton-Block (Clay)", 66),
    Instrument("bell", "Glocke", "Goldblock", 78),
    Instrument("chime", "Glockenspiel", "Packeis", 78),
    Instrument("xylophone", "Xylophon", "Knochenblock", 78),
    Instrument("iron_xylophone", "Vibraphon", "Eisenblock", 54),
    Instrument("cow_bell", "Kuhglocke", "Seelensand", 66),
    Instrument("bit", "Bit (8-Bit)", "Smaragdblock", 54),
    Instrument("banjo", "Banjo", "Heuballen", 54),
    Instrument("pling", "Pling (E-Piano)", "Leuchtstein", 54),
    Instrument("basedrum", "Bassdrum", "Stein (o. Bruchstein, Erz…)", 54, True),
    Instrument("snare", "Snare", "Sand (o. Kies, Beton-Pulver)", 54, True),
    Instrument("hat", "Hi-Hat/Klick", "Glas (o. Seelaterne)", 54, True),
]}


def instrument_for_program(program: int) -> str:
    """General-MIDI-Programm (0-127) -> Notenblock-Instrument."""
    p = program
    if p < 8:
        return "harp"
    if p < 16:
        return {8: "bell", 9: "bell", 10: "chime", 11: "iron_xylophone", 12: "xylophone",
                13: "xylophone", 14: "chime", 15: "iron_xylophone"}[p]
    if p < 24:
        return "flute" if p in (20, 21, 22, 23) else "harp"   # Orgeln / Akkordeon
    if p < 32:
        return "guitar"
    if p < 40:
        return "bass"
    if p < 56:
        return "flute" if p in (40, 41) else "harp"            # Streicher / Ensemble
    if p < 64:
        return "didgeridoo" if p in (58, 59) else "harp"       # Blech
    if p < 80:
        return "flute"                                          # Holzbläser / Pfeifen
    if p < 88:
        return "bit"                                            # Synth-Lead
    if p < 104:
        return "pling"                                          # Pads / FX
    if p == 105 or p == 106 or p == 107:
        return "banjo"
    if p < 112:
        return "harp"
    if p == 112:
        return "bell"
    if p == 113:
        return "cow_bell"
    if p == 114:
        return "iron_xylophone"
    if p < 120:
        return "basedrum"
    return "hat"


def drum_for_gm(note: int):
    """GM-Drum-Note (Kanal 10) -> (Instrument, Klicks)."""
    if note in (35, 36):
        return "basedrum", 0
    if note in (41, 43, 45, 47, 48, 50):          # Toms: Bassdrum mit Tonhöhe
        return "basedrum", {41: 4, 43: 6, 45: 9, 47: 11, 48: 14, 50: 16}[note]
    if note in (38, 40):
        return "snare", 8
    if note in (39, 54, 70, 82):                  # Clap, Tambourin, Shaker
        return "snare", 16
    if note in (42, 44):
        return "hat", 18
    if note == 46:
        return "hat", 12
    if note in (49, 51, 52, 53, 55, 57, 59):      # Becken
        return "snare", 22
    if note in (56,):
        return "cow_bell", 8
    if note in (37, 75, 76, 77):                  # Rimshot, Claves, Woodblock
        return "hat", 6
    if note in (60, 61, 62, 63, 64, 65, 66):      # Bongos/Congas/Timbales
        return "basedrum", 18
    return "hat", 10
