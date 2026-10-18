"""Structural regressions using small AAFs with real embedded PCM sources."""
import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import warnings
import wave
from fractions import Fraction

import aaf2
import numpy as np
import AAF2AAF as converter


class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='aaf2aaf_test_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / 'input.aaf'
        self.output = self.root / 'output.aaf'
        self.wav = self.root / 'source.wav'
        with wave.open(str(self.wav), 'wb') as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(48000)
            stream.writeframes(b'\0\0' * 1000)

    def make_aaf(self, wrapped=False, left_start=0, right_start=20,
                 left_limit=1000, fade_name='Fade 1', single=None,
                 unknown=False, nested=False, picture=False, automation=False,
                 multiple_inputs=False, successive=False, edit_rate=48000,
                 fillers=False):
        samples_per_unit = Fraction(48000) / edit_rate
        if edit_rate != 48000:
            with wave.open(str(self.wav), 'wb') as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(48000)
                stream.writeframes(b'\0\0' * int(1000 * samples_per_unit))
        with aaf2.open(str(self.input), 'w') as f:
            operation = f.create.OperationDef(
                aaf2.auid.AUID('9d2ea894-0968-11d3-8a38-0050040ef7d2'), 'MonoAudioGain')
            operation.media_kind = 'sound'
            operation.number_inputs = 1
            f.dictionary.register_def(operation)
            comp = f.create.CompositionMob('Regression')
            f.content.mobs.append(comp)
            seq = comp.create_sound_slot(edit_rate).segment
            entries = [('Left', left_start, 100), (fade_name, 0, 10), ('Right', right_start, 100)]
            if single == 'in':
                entries = entries[1:]
            elif single == 'out':
                entries = entries[:2]
            elif single == 'both':
                entries = [('Fade 1', 0, 10), ('Both', right_start, 100), ('Fade 2', 0, 10)]
            if successive:
                entries.insert(2, ('Fade 2', 0, 10))
            for name, start, length in entries:
                master = f.create.MasterMob(name)
                f.content.mobs.append(master)
                master.import_audio_essence(str(self.wav), edit_rate=edit_rate)
                if name == 'Left':
                    physical = master.slots[0].segment.mob
                    physical.descriptor.length = int(left_limit * samples_per_unit)
                    if unknown:
                        # A rate mismatch makes the descriptor's length unverified.
                        physical.descriptor['SampleRate'].value = 24000
                clip = master.create_source_clip(1, start, length, 'sound')
                if wrapped:
                    group = f.create.OperationGroup(operation, length, 'sound')
                    group.segments.append(clip)
                    if multiple_inputs:
                        group.segments.append(master.create_source_clip(1, start, length, 'sound'))
                    if automation:
                        param_id = aaf2.auid.AUID('e4962320-2267-11d3-8a4c-0050040ef7d2')
                        param_def = f.dictionary.lookup_parameterdef(param_id)
                        if param_def is None:
                            param_def = f.create.ParameterDef(param_id, 'Level', '', 'Rational')
                            f.dictionary.register_def(param_def)
                        interp = f.dictionary.lookup_interperlationdef(aaf2.misc.LinearInterp)
                        if interp is None:
                            interp = f.create.InterpolationDef(aaf2.misc.LinearInterp, 'LinearInterp')
                            f.dictionary.register_def(interp)
                        parameter = f.create.VaryingValue(param_def, interp)
                        parameter.add_keyframe(0, Fraction(1, 4))
                        parameter.add_keyframe(1, Fraction(3, 4))
                        group.parameters.append(parameter)
                    clip = group
                    if nested:
                        group = f.create.OperationGroup(operation, length, 'sound')
                        group.segments.append(clip)
                        clip = group
                seq.components.append(clip)
            if fillers:
                seq.components.insert(0, f.create.Filler('sound', 25))
                seq.components.append(f.create.Filler('sound', 35))
            if picture:
                seq.media_kind = 'picture'
            seq.length = converter.sequence_duration(seq.components)

    def convert(self, **kwargs):
        kwargs.setdefault('curve', 'linear')
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            with contextlib.redirect_stdout(io.StringIO()):
                converter.process_fades(str(self.input), str(self.output), **kwargs)
        return caught

    @contextlib.contextmanager
    def result(self):
        with aaf2.open(str(self.output), 'r') as f:
            mob = next(m for m in f.content.mobs if isinstance(m, aaf2.mobs.CompositionMob))
            yield mob.slots[0].segment

    def test_crossfades_preserve_duration_and_source_positions(self):
        for wrapped, nested in [(False, False), (True, False), (True, True)]:
            with self.subTest(wrapped=wrapped, nested=nested):
                self.make_aaf(wrapped=wrapped, nested=nested)
                self.assertEqual(self.convert(), [])
                with self.result() as seq:
                    self.assertEqual(seq.length, 210)
                    self.assertEqual(converter.sequence_duration(seq.components), 210)
                    self.assertEqual([c.length for c in seq.components], [110, 10, 110])
                    self.assertIsInstance(seq.components[1], aaf2.components.Transition)
                    left, _ = converter.source_chain(seq.components[0])
                    right, _ = converter.source_chain(seq.components[2])
                    self.assertEqual((left.start, left.length), (0, 110))
                    self.assertEqual((right.start, right.length), (10, 110))

    def test_unconvertible_fades_are_kept_without_shortening(self):
        for case in [dict(right_start=3), dict(right_start=0), dict(left_limit=103),
                     dict(left_limit=90), dict(unknown=True), dict(single='in', right_start=3),
                     dict(single='out', left_limit=103)]:
            with self.subTest(case=case):
                self.make_aaf(**case)
                self.assertTrue(self.convert())
                expected = 110 if case.get('single') else 210
                with self.result() as seq:
                    self.assertEqual(seq.length, expected)
                    self.assertEqual(converter.sequence_duration(seq.components), expected)
                    self.assertFalse(any(isinstance(c, aaf2.components.Transition) for c in seq.components))
                    self.assertTrue(any(converter.is_rendered_fade(c) for c in seq.components))

    def test_protools_names_and_frame_based_timeline(self):
        self.make_aaf(edit_rate=Fraction(24000, 1001), fade_name='Fade ')
        self.assertEqual(self.convert(), [])
        with self.result() as seq:
            self.assertEqual(seq.length, 210)
            self.assertEqual(converter.sequence_duration(seq.components), 210)
            self.assertIsInstance(seq.components[1], aaf2.components.Transition)
            source, _ = converter.source_chain(seq.components[0])
            self.assertEqual(converter.source_bounds(source, Fraction(24000, 1001)), (0, 1000))

    def test_one_sided_fades_use_boundary_cutpoints_and_keep_timing(self):
        for side in ['in', 'out']:
            for fillers in [False, True]:
                for wrapped in [False, True]:
                    with self.subTest(side=side, fillers=fillers, wrapped=wrapped):
                        self.make_aaf(single=side, fillers=fillers, wrapped=wrapped)
                        self.assertEqual(self.convert(), [])
                        with self.result() as seq:
                            expected_duration = 170 if fillers else 110
                            self.assertEqual(seq.length, expected_duration)
                            self.assertEqual(converter.sequence_duration(seq.components), expected_duration)
                            transitions = [(pos, c) for _, pos, c in seq.positions()
                                           if isinstance(c, aaf2.components.Transition)]
                            self.assertEqual(len(transitions), 1)
                            pos, trans = transitions[0]
                            self.assertEqual(trans.cutpoint, 0 if side == 'in' else 10)
                            self.assertEqual(pos, (25 if fillers else 0) + (0 if side == 'in' else 100))
                            sources = [converter.source_chain(c)[0] for c in seq.components]
                            source = next(s for s in sources if s is not None)
                            self.assertEqual((source.start, source.length),
                                             (10, 110) if side == 'in' else (0, 110))

    def test_both_fades_on_one_clip_keep_its_full_length(self):
        self.make_aaf(single='both', wrapped=True, automation=True, fillers=True)
        self.assertEqual(self.convert(), [])
        with self.result() as seq:
            self.assertEqual(converter.sequence_duration(seq.components), 180)
            self.assertEqual(seq.length, 180)
            transitions = [(pos, c.length, c.cutpoint) for _, pos, c in seq.positions()
                           if isinstance(c, aaf2.components.Transition)]
            self.assertEqual(transitions, [(25, 10, 0), (135, 10, 10)])
            source, groups = converter.source_chain(seq.components[2])
            self.assertEqual((source.start, source.length), (10, 120))
            parameter = list(groups[0].parameters)[0]
            self.assertAlmostEqual(parameter.pointlist[1].time * 120, 10)
            self.assertAlmostEqual(parameter.pointlist[2].time * 120, 110)

    def test_ordinary_clip_name_containing_fade_is_not_converted(self):
        self.make_aaf(fade_name='Music Fade Away')
        self.assertEqual(self.convert(), [])
        with self.result() as seq:
            self.assertEqual([c.length for c in seq.components], [100, 10, 100])
            self.assertEqual(seq.length, 210)

    def test_automation_keeps_existing_events_and_holds_extended_edges(self):
        self.make_aaf(wrapped=True, automation=True)
        self.assertEqual(self.convert(), [])
        with self.result() as seq:
            left = list(seq.components[0].parameters)[0]
            right = list(seq.components[2].parameters)[0]
            for point, expected in zip(left.pointlist, [(0, .25), (100 / 110, .75), (1, .75)]):
                self.assertAlmostEqual(point.time, expected[0])
                self.assertAlmostEqual(point.value, expected[1])
            for point, expected in zip(right.pointlist, [(0, .25), (10 / 110, .25), (1, .75)]):
                self.assertAlmostEqual(point.time, expected[0])
                self.assertAlmostEqual(point.value, expected[1])
            self.assertEqual(len(left.pointlist), 3)
            self.assertEqual(len(right.pointlist), 3)

    def test_ambiguous_groups_and_successive_fades_are_kept(self):
        for case in [dict(wrapped=True, multiple_inputs=True), dict(successive=True)]:
            with self.subTest(case=case):
                self.make_aaf(**case)
                self.convert()
                with self.result() as seq:
                    self.assertFalse(any(isinstance(c, aaf2.components.Transition) for c in seq.components))
                    expected = 220 if case.get('successive') else 210
                    self.assertEqual(converter.sequence_duration(seq.components), expected)

    def test_picture_sequence_is_unchanged(self):
        self.make_aaf(picture=True)
        self.assertEqual(self.convert(), [])
        with self.result() as seq:
            self.assertEqual([c.length for c in seq.components], [100, 10, 100])

    def test_cli_runs_conversion_once(self):
        self.make_aaf()
        result = subprocess.run(
            [sys.executable, '-B', str(Path(converter.__file__)), str(self.input), str(self.output)],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count('Un-rendering fades'), 1)
        self.assertEqual(result.stdout.count('Successfully generated'), 1)

    def test_cli_missing_input_has_nonzero_exit_and_no_output(self):
        result = subprocess.run(
            [sys.executable, '-B', str(Path(converter.__file__)), str(self.input), str(self.output)],
            capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())

    def test_input_cannot_be_overwritten(self):
        self.make_aaf()
        original = self.input.read_bytes()
        with self.assertRaises(ValueError):
            converter.process_fades(str(self.input), str(self.input))
        self.assertEqual(self.input.read_bytes(), original)

    def test_failed_conversion_preserves_existing_output_and_input(self):
        self.make_aaf()
        before = self.input.read_bytes()
        self.output.write_bytes(b'previous successful output')
        with patch('AAF2AAF.rewrite_fades', side_effect=ValueError('forced failure')):
            with self.assertRaises(ValueError):
                self.convert()
        self.assertEqual(self.output.read_bytes(), b'previous successful output')
        self.assertEqual(self.input.read_bytes(), before)
        self.assertEqual(list(self.root.glob('.aaf2aaf-*')), [])

    def test_report_and_strict_cli_expose_unconverted_fades(self):
        self.make_aaf(right_start=0)
        report = self.root / 'report.json'
        result = subprocess.run([sys.executable, '-B', str(Path(converter.__file__)),
                                 str(self.input), str(self.output), '--report', str(report), '--strict'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        import json
        data = json.loads(report.read_text())
        self.assertEqual(data['converted'], [])
        self.assertIn('insufficient handles', data['preserved'][0]['reason'])
        self.assertTrue(self.output.exists())

    def test_valid_multiple_input_mixer_extends_all_inputs(self):
        self.make_aaf(wrapped=True, multiple_inputs=True)
        with aaf2.open(str(self.input), 'rw') as f:
            mixer = f.create.OperationDef(aaf2.auid.AUID('8d896ad0-2261-11d3-8a4c-0050040ef7d2'), 'MonoAudioMixdown')
            mixer.media_kind = 'sound'
            mixer.number_inputs = 2
            f.dictionary.register_def(mixer)
            comp = next(m for m in f.content.mobs if isinstance(m, aaf2.mobs.CompositionMob))
            seq = comp.slots[0].segment
            for index in (0, 2):
                seq.components[index].operation = mixer
            # The rendered fade remains a single-input clip, not a two-input mix.
            fade_group = seq.components[1]
            fade_group.segments.pop(-1)
        self.assertEqual(self.convert(), [])
        with self.result() as seq:
            self.assertEqual(converter.sequence_duration(seq.components), 210)
            for index in (0, 2):
                self.assertEqual(len(seq.components[index].segments), 2)
                self.assertTrue(all(s.length == 110 for s in seq.components[index].segments))

    def test_nested_sequence_is_converted_without_flattening_effect(self):
        self.make_aaf(wrapped=True)
        with aaf2.open(str(self.input), 'rw') as f:
            comp = next(m for m in f.content.mobs if isinstance(m, aaf2.mobs.CompositionMob))
            seq = comp.slots[0].segment
            operation = seq.components[0].operation
            wrapper = f.create.OperationGroup(operation, seq.length, 'sound')
            # Detach before reattaching the strong-referenced sequence.
            comp.slots[0].segment = f.create.Filler('sound', 210)
            wrapper.segments.append(seq)
            comp.slots[0].segment = wrapper
        self.assertEqual(self.convert(), [])
        with self.result() as wrapper:
            self.assertIsInstance(wrapper, aaf2.components.OperationGroup)
            self.assertEqual(wrapper.length, 210)
            seq = wrapper.segments[0]
            self.assertIsInstance(seq.components[1], aaf2.components.Transition)

    def test_bezier_and_cubic_automation_keep_their_old_curve(self):
        for interpolation_id in [aaf2.misc.BezierInterpolator, aaf2.misc.CubicInterpolator]:
            with self.subTest(interpolation=interpolation_id):
                self.make_aaf(wrapped=True, automation=True, single='both')
                with aaf2.open(str(self.input), 'rw') as f:
                    if interpolation_id == aaf2.misc.BezierInterpolator:
                        from uuid import uuid5, NAMESPACE_URL
                        for side in ('IN', 'OUT'):
                            for axis in ('POS', 'VAL'):
                                name = f'PP_{side}_TANGENT_{axis}_U'
                                definition = f.create.ParameterDef(aaf2.auid.AUID(uuid5(NAMESPACE_URL, 'test-only:' + name)),
                                                                   name, '', 'Rational')
                                f.dictionary.register_def(definition)
                    comp = next(m for m in f.content.mobs if isinstance(m, aaf2.mobs.CompositionMob))
                    parameter = list(comp.slots[0].segment.components[1].parameters)[0]
                    interp = f.create.InterpolationDef(interpolation_id, 'Curved')
                    f.dictionary.register_def(interp)
                    parameter.interpolationdef = interp
                    parameter.pointlist.value = []
                    for time, value in [(0, .2), (.2, .8), (.7, .35), (1, .6)]:
                        point = parameter.add_keyframe(time, aaf2.rational.AAFRational(value))
                        if interpolation_id == aaf2.misc.BezierInterpolator:
                            converter.set_tangent(f, point, 'IN', (-.04, -.1))
                            converter.set_tangent(f, point, 'OUT', (.04, .15))
                    old_values = [parameter.value_at(i / 100) for i in range(101)]
                self.assertEqual(self.convert(), [])
                with self.result() as seq:
                    group = next(c for c in seq.components if isinstance(c, aaf2.components.OperationGroup))
                    parameter = list(group.parameters)[0]
                    for i, expected in enumerate(old_values):
                        self.assertAlmostEqual(parameter.value_at((10+i)/120), expected, places=6)
                    self.assertAlmostEqual(parameter.value_at(0), .2)
                    self.assertAlmostEqual(parameter.value_at(1), .6)

    def test_consecutive_modes_keep_duration_and_original_body_positions(self):
        for mode in ('split', 'merge'):
            with self.subTest(mode=mode):
                self.make_aaf(successive=True)
                self.assertEqual(self.convert(consecutive=mode), [])
                with self.result() as seq:
                    self.assertEqual(converter.sequence_duration(seq.components), 220)
                    transitions = [(pos, c.length, c.cutpoint) for _, pos, c in seq.positions()
                                   if isinstance(c, aaf2.components.Transition)]
                    self.assertEqual(transitions, [(100, 10, 10), (110, 10, 0)] if mode == 'split'
                                     else [(100, 20, 10)])
                    last = seq.components[-1]
                    last_pos = list(seq.positions())[-1][1]
                    self.assertEqual(last_pos + 20 - last.start, 120)

    def make_audio_aaf(self, curve='linear', consecutive=None, channels=1):
        random = np.random.default_rng(42)
        full, start, body, length = 5000, 1500, 1000, 512
        left = np.round(random.uniform(-.3, .3, (full, channels)) * 32767).astype('<i2')
        right = np.round(random.uniform(-.3, .3, (full, channels)) * 32767).astype('<i2')
        total = length * 2 if consecutive == 'merge' else length
        t = np.linspace(0, 1, total)[:, None]
        gain = {'linear': lambda v: v, 'sqrt-power': np.sqrt,
                'sine': lambda v: np.sin(v*np.pi/2), 'unknown': lambda v: v*v}[curve]
        a = left[start+body:start+body+total].astype(float) / 32768
        b = right[start-total:start].astype(float) / 32768
        if consecutive == 'split':
            rendered = [a * gain(1-t), b * gain(t)]
        else:
            combined = a * gain(1-t) + b * gain(t)
            rendered = list(np.split(combined, 2)) if consecutive == 'merge' else [combined]
        arrays = [('Left', left)] + [(f'Fade {i+1}', np.rint(v*32768).astype('<i2'))
                                     for i, v in enumerate(rendered)] + [('Right', right)]
        with aaf2.open(str(self.input), 'w') as f:
            comp = f.create.CompositionMob('Audible curve regression')
            f.content.mobs.append(comp)
            seq = comp.create_sound_slot(48000).segment
            for i, (name, audio) in enumerate(arrays):
                path = self.root / f'pcm_{i}.wav'
                with wave.open(str(path), 'wb') as stream:
                    stream.setnchannels(channels)
                    stream.setsampwidth(2)
                    stream.setframerate(48000)
                    stream.writeframes(audio.tobytes())
                master = f.create.MasterMob(name)
                f.content.mobs.append(master)
                master.import_audio_essence(str(path))
                fade = name.startswith('Fade')
                seq.components.append(master.create_source_clip(1, 0 if fade else start,
                                                               len(audio) if fade else body, 'sound'))
            seq.length = converter.sequence_duration(seq.components)

    def test_auto_curve_verifies_all_three_families_and_writes_explicit_levels(self):
        for curve in ('linear', 'sqrt-power', 'sine'):
            with self.subTest(curve=curve):
                self.make_audio_aaf(curve)
                self.assertEqual(self.convert(curve='auto', target='generic'), [])
                with self.result() as seq:
                    self.assertIsInstance(seq.components[1], aaf2.components.Transition)
                    parameter = list(seq.components[1]['OperationGroup'].value.parameters)[0]
                    for time in np.linspace(.001, 1, 100):
                        expected = time if curve == 'linear' else np.sqrt(time) if curve == 'sqrt-power' else np.sin(time*np.pi/2)
                        self.assertAlmostEqual(parameter.value_at(float(time)), expected, delta=1e-5)

    def test_unknown_and_silent_curves_are_not_guessed(self):
        self.make_audio_aaf('unknown')
        self.assertTrue(self.convert(curve='auto'))
        with self.result() as seq:
            self.assertTrue(converter.is_rendered_fade(seq.components[1]))
        self.make_aaf()
        self.assertTrue(self.convert(curve='auto'))

    def test_protools_sine_curve_uses_native_power_interpolation(self):
        self.make_audio_aaf('sine')
        self.assertEqual(self.convert(curve='auto'), [])
        with self.result() as seq:
            parameter = list(seq.components[1]['OperationGroup'].value.parameters)[0]
            self.assertEqual(str(parameter.interpolationdef.auid), 'c09153f7-bd18-4e5a-ad09-cbdd654fa001')
            self.assertEqual([(p.time, p.value) for p in parameter.pointlist], [(0, 0), (1, 1)])

    def test_protools_does_not_silently_substitute_a_different_power_curve(self):
        self.make_audio_aaf('sqrt-power')
        self.assertTrue(self.convert(curve='auto'))
        with self.result() as seq:
            self.assertTrue(converter.is_rendered_fade(seq.components[1]))

    def test_stereo_media_use_the_stereo_dissolve_definition(self):
        self.make_audio_aaf(channels=2)
        self.assertEqual(self.convert(curve='auto'), [])
        with self.result() as seq:
            operation = seq.components[1]['OperationGroup'].value.operation
            self.assertEqual(str(operation.auid), '0c3bea42-fc05-11d2-8a29-0050040ef7d2')

    def test_consecutive_fades_can_be_disambiguated_from_audio(self):
        for mode in ('merge', 'split'):
            with self.subTest(mode=mode):
                self.make_audio_aaf(consecutive=mode)
                self.assertEqual(self.convert(curve='auto'), [])
                with self.result() as seq:
                    transitions = [c for c in seq.components if isinstance(c, aaf2.components.Transition)]
                    self.assertEqual(len(transitions), 1 if mode == 'merge' else 2)
                    self.assertEqual(converter.sequence_duration(seq.components), 3024)


if __name__ == '__main__':
    unittest.main()
