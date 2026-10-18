"""Read PCM used by a fade and verify a supported curve against rendered audio."""
from fractions import Fraction
from pathlib import Path
from urllib.parse import unquote, urlparse
import wave

import aaf2
import numpy as np


class AudioUnavailable(ValueError):
    pass


def decode_pcm(raw, width, channels):
    if width == 1:
        values = (np.frombuffer(raw, dtype=np.uint8).astype(float) - 128) / 128
    elif width in (2, 4):
        values = np.frombuffer(raw, dtype='<i' + str(width)).astype(float) / (2 ** (8 * width - 1))
    elif width == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        values = b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16)
        values = ((values << 8) >> 8).astype(float) / 8388608
    else:
        raise AudioUnavailable('unsupported PCM sample width')
    return values.reshape(-1, channels)


class MediaReader:
    def __init__(self, file, source_directory):
        self.file = file
        self.directory = Path(source_directory)
        self.verified_channels = None

    def source(self, clip, start, length, edit_rate):
        visited = set()
        while clip.mob is not None:
            mob = clip.mob
            key = (str(mob.mob_id), clip.slot_id)
            if key in visited:
                raise AudioUnavailable('cyclic media reference')
            visited.add(key)
            slot = mob.slot_at(clip.slot_id)
            if slot.edit_rate != edit_rate or slot.origin != 0:
                raise AudioUnavailable('unsupported source edit rate or origin')
            if isinstance(mob, aaf2.mobs.SourceMob):
                return self.physical(mob, start, length, edit_rate)
            child = slot.segment
            if not isinstance(child, aaf2.components.SourceClip):
                raise AudioUnavailable('nonlinear source-mob structure')
            start += child.start
            clip = child
        raise AudioUnavailable('missing media reference')

    def physical(self, mob, start, length, edit_rate):
        descriptor = mob.descriptor
        rate = descriptor['SampleRate'].value
        sample_start = Fraction(start) * Fraction(rate) / Fraction(edit_rate)
        count = Fraction(length) * Fraction(rate) / Fraction(edit_rate)
        if sample_start.denominator != 1 or count.denominator != 1:
            raise AudioUnavailable('fade boundaries do not map to whole audio samples')
        sample_start, count = int(sample_start), int(count)
        if sample_start < 0 or sample_start + count > descriptor.length:
            raise AudioUnavailable('audio request exceeds source media')
        essence = self.file.content.essencedata.get(mob.mob_id, None)
        if essence is not None and isinstance(descriptor, aaf2.essence.PCMDescriptor):
            width = descriptor['QuantizationBits'].value // 8
            channels = descriptor['Channels'].value
            stream = essence.open('r')
            stream.seek(sample_start * width * channels)
            raw = stream.read(count * width * channels)
        else:
            media = None
            for locator in descriptor['Locator'].value or []:
                if 'URLString' not in locator:
                    continue
                path = unquote(urlparse(locator['URLString'].value).path)
                name = path.rsplit('/', 1)[-1]
                candidates = [Path(path), self.directory / 'Audio Files' / name, self.directory / name]
                # A file URI for a Windows drive starts with a leading slash.
                if len(path) > 3 and path[0] == '/' and path[2] == ':':
                    candidates.insert(0, Path(path[1:]))
                media = next((p for p in candidates if p.is_file()), None)
                if media:
                    break
            if essence is not None:
                source = essence.open('r')
            elif media is not None:
                source = str(media)
            else:
                raise AudioUnavailable('external audio not found beside the input AAF')
            try:
                with wave.open(source, 'rb') as reader:
                    if reader.getframerate() != rate or reader.getnframes() < sample_start + count:
                        raise AudioUnavailable('WAVE header does not match the AAF descriptor')
                    width, channels = reader.getsampwidth(), reader.getnchannels()
                    reader.setpos(sample_start)
                    raw = reader.readframes(count)
            except wave.Error as error:
                raise AudioUnavailable(f'unsupported WAVE encoding: {error}') from error
        if len(raw) != count * width * channels:
            raise AudioUnavailable('truncated media')
        return decode_pcm(raw, width, channels)

    def component(self, component, start, length, edit_rate):
        if isinstance(component, aaf2.components.Sequence) and len(component.components) == 1:
            return self.component(component.components[0], start, length, edit_rate)
        if isinstance(component, aaf2.components.SourceClip):
            return self.source(component, component.start + start, length, edit_rate)
        if isinstance(component, aaf2.components.OperationGroup):
            gain_ids = ('9d2ea894-0968-11d3-8a38-0050040ef7d2',
                        '9d2ea895-0968-11d3-8a38-0050040ef7d2')
            if str(component.operation.auid) not in gain_ids or len(component.segments) != 1:
                raise AudioUnavailable('curve verification requires a source clip or audio gain group')
            audio = self.component(component.segments[0], start, length, edit_rate)
            for parameter in component.parameters:
                if parameter.parameterdef.name.lower() not in ('level', 'amplitude'):
                    raise AudioUnavailable('unknown gain parameter')
                if isinstance(parameter, aaf2.misc.ConstantValue):
                    audio *= float(parameter.value)
                else:
                    # Evaluate at audio sample positions, clamping extended handles.
                    times = np.clip((start + np.arange(len(audio)) * float(length) / len(audio)) /
                                    component.length, 0, 1)
                    points = list(parameter.pointlist)
                    if parameter.interpolationdef.auid != aaf2.misc.LinearInterp:
                        raise AudioUnavailable('curve verification of this gain interpolation is unavailable')
                    audio *= np.interp(times, [p.time for p in points], [p.value for p in points])[:, None]
            return audio
        raise AudioUnavailable('curve verification of this component structure is unavailable')


def infer_curve(reader, render, previous, following, length, edit_rate):
    actual = (np.concatenate([reader.component(r, 0, r.length, edit_rate) for r in render])
              if isinstance(render, (list, tuple)) else reader.component(render, 0, length, edit_rate))
    previous_silent = previous is None or isinstance(previous, aaf2.components.Filler)
    following_silent = following is None or isinstance(following, aaf2.components.Filler)
    outgoing = np.zeros_like(actual) if previous_silent else reader.component(previous, previous.length, length, edit_rate)
    incoming = np.zeros_like(actual) if following_silent else reader.component(following, -length, length, edit_rate)
    if actual.shape != outgoing.shape or actual.shape != incoming.shape:
        raise AudioUnavailable('source and rendered channel layouts differ')
    reader.verified_channels = actual.shape[1]
    rms = float(np.sqrt(np.mean(actual * actual)))
    if not np.isfinite(rms) or rms < 1e-6:
        raise AudioUnavailable('rendered audio is silent; its original curve is not identifiable')
    errors = {}
    # Renderers differ in whether the endpoint falls on the last sample or
    # just after it. Check both conventions without changing the curve family.
    for t in (np.linspace(0, 1, len(actual)), np.arange(len(actual)) / len(actual)):
        t = t[:, None]
        for name, fade_in, fade_out in [('linear', t, 1-t),
                                    ('sqrt-power', np.sqrt(t), np.sqrt(1-t)),
                                        ('sine', np.sin(t*np.pi/2), np.cos(t*np.pi/2))]:
            predicted = incoming * fade_in + outgoing * fade_out
            error = float(np.sqrt(np.mean((predicted-actual) ** 2))) / rms
            errors[name] = min(errors.get(name, float('inf')), error)
    ranked = sorted(errors, key=errors.get)
    best, other = ranked[:2]
    if errors[best] > 0.0002 or errors[other] - errors[best] < 0.01:
        raise AudioUnavailable('render does not uniquely match a supported curve family')
    return best, errors[best]
