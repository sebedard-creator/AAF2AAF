"""Author a separate audible import diagnostic using a native Pro Tools AAF scaffold.

This creates new test audio and a new test timeline. It does not recover or
convert the reference session's audio. The reference and its media stay intact.
"""
import argparse
from fractions import Fraction
from pathlib import Path
import shutil
import struct
import sys
from urllib.parse import quote, unquote, urlparse
import wave

import aaf2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import AAF2AAF as converter


def write_pcm24(path, values, sample_rate):
    samples = np.rint(np.clip(values, -1, 1) * 8388607).astype(np.int32)
    packed = np.column_stack((samples & 255, (samples >> 8) & 255,
                              (samples >> 16) & 255)).astype(np.uint8)
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(3)
        stream.setframerate(sample_rate)
        stream.writeframes(packed.tobytes())
    summary = bytearray(path.read_bytes()[:44])
    # WAVE summaries describe the header, with the data chunk length retained.
    struct.pack_into('<I', summary, 4, len(summary) - 8)
    return summary


def create_diagnostic(reference, destination):
    if destination.exists():
        raise ValueError(f'Diagnostic destination must be a new directory: {destination}')
    sample_rate, full_length, body_length, fade_length, source_start = 48000, 240, 48, 12, 48
    with aaf2.open(str(reference), 'r') as f:
        tracks = [(mob, slot) for mob in f.content.mobs
                  if isinstance(mob, aaf2.mobs.CompositionMob)
                  for slot in mob.slots
                  if isinstance(slot.segment, aaf2.components.Sequence)
                  and (slot.segment.media_kind or '').lower() == 'sound']
        if len(tracks) != 1:
            raise ValueError('Expected the single-track, five-fade Pro Tools test export.')
        _, slot = tracks[0]
        units = Fraction(sample_rate) / Fraction(slot.edit_rate)
        if units.denominator != 1:
            raise ValueError('Diagnostic requires an integral number of samples per frame.')
        unit = int(units)
        sources = [c for c in slot.segment.components if isinstance(c, aaf2.components.SourceClip)]
        if len(sources) != 10 or [converter.is_rendered_fade(c) for c in sources] != [
                True, False, False, True, True, False, True, False, True, False]:
            raise ValueError('Expected fade-in, fade-out, both, and crossfade in that order.')

    destination.mkdir(parents=True)
    media = destination / 'Audio Files'
    media.mkdir()
    baseline = destination / 'diagnostic_rendered.aaf'
    output = destination / 'diagnostic_editable.aaf'
    shutil.copyfile(reference, baseline)

    # Each underlying clip is a different tone, at a modest level.
    signals = [0.12 * np.sin(2 * np.pi * hz * np.arange(full_length * unit) / sample_rate)
               for hz in (220, 330, 440, 550, 660)]
    ramp = np.linspace(0, 1, fade_length * unit)
    head = slice((source_start - fade_length) * unit, source_start * unit)
    tail = slice((source_start + body_length) * unit,
                 (source_start + body_length + fade_length) * unit)
    generated = [
        ('01_fade_in_render', signals[0][head] * ramp),
        ('01_fade_in_source', signals[0]),
        ('02_fade_out_source', signals[1]),
        ('02_fade_out_render', signals[1][tail] * (1 - ramp)),
        ('03_both_in_render', signals[2][head] * ramp),
        ('03_both_source', signals[2]),
        ('03_both_out_render', signals[2][tail] * (1 - ramp)),
        ('04_cross_left', signals[3]),
        ('04_cross_render', signals[3][tail] * (1 - ramp) + signals[4][head] * ramp),
        ('04_cross_right', signals[4]),
    ]
    with aaf2.open(str(baseline), 'rw') as f:
        mob, slot = next((m, s) for m in f.content.mobs
                         if isinstance(m, aaf2.mobs.CompositionMob)
                         for s in m.slots if isinstance(s.segment, aaf2.components.Sequence)
                         and (s.segment.media_kind or '').lower() == 'sound')
        mob.name = 'AAF2AAF diagnostic - generated tones - 12 frame fades'
        old_sources = [c for c in slot.segment.components if isinstance(c, aaf2.components.SourceClip)]
        new_sources = []
        for original, (name, values) in zip(old_sources, generated):
            master = original.mob
            master_slot = master.slot_at(original.slot_id)
            physical_ref = master_slot.segment
            physical = physical_ref.mob
            physical_slot = physical.slot_at(physical_ref.slot_id)
            descriptor = physical.descriptor
            if not isinstance(descriptor, aaf2.essence.WAVEDescriptor):
                raise ValueError('Expected external WAVE media in the native scaffold.')
            summary = write_pcm24(media / (name + '.wav'), values, sample_rate)
            descriptor['Summary'].value = list(summary)
            descriptor.length = len(values)
            physical.name = name
            master_slot.name = name
            duration = len(values) // unit
            physical_slot.segment.length = duration
            physical_ref.start = 0
            physical_ref.length = duration
            locator = descriptor['Locator'].value[0]
            old_path = unquote(urlparse(locator['URLString'].value).path)
            parent = old_path.rsplit('/' + reference.parent.name + '/', 1)[0]
            locator['URLString'].value = 'file://' + quote(
                parent + '/' + destination.name + '/Audio Files/' + name + '.wav', safe='/:')
            is_fade = converter.is_rendered_fade(original)
            new_sources.append(master.create_source_clip(original.slot_id,
                               0 if is_fade else source_start,
                               fade_length if is_fade else body_length, 'sound'))

        # Four independent cases, separated by one second of silence.
        seq = f.create.Sequence('sound')
        seq.components.append(f.create.Filler('sound', 864000))
        for i, clip in enumerate(new_sources):
            seq.components.append(clip)
            if i in (1, 3, 6, 9):
                seq.components.append(f.create.Filler('sound', 24))
        seq.length = converter.sequence_duration(seq.components)
        slot.segment = seq

    converter.process_fades(str(baseline), str(output), curve='auto')
    return baseline, output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reference_aaf', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    for path in create_diagnostic(args.reference_aaf, args.destination):
        print(path)
