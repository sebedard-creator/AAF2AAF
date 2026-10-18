import os
import argparse
import json
import math
import re
import shutil
import tempfile
import warnings
from fractions import Fraction
import aaf2

FADE_NAME = re.compile(
    r'Fade(?:[ _-]+\d+)?(?:\.(?:L|R|C|Ls|Rs|LFE))?(?:\.(?:wav|aif|aiff))?',
    re.IGNORECASE,
)


def source_chain(component):
    """Return a source and its duration-preserving, single-input wrappers."""
    groups = []
    while isinstance(component, (aaf2.components.OperationGroup, aaf2.components.Sequence)):
        sequence = isinstance(component, aaf2.components.Sequence)
        children = component.components if sequence else component.segments
        if (len(children) != 1 or
                (not sequence and component.operation['IsTimeWarp'].value)):
            return None, []
        child = children[0]
        if child.length != component.length:
            return None, []
        if not sequence:
            groups.append(component)
        component = child
    if (isinstance(component, aaf2.components.SourceClip) and
            (component.media_kind or '').lower() == 'sound'):
        return component, groups
    return None, []


def extension_nodes(component, at_start):
    """Collect every input to extend, retaining the existing effect hierarchy."""
    if isinstance(component, aaf2.components.SourceClip):
        return ([component], [], []) if (component.media_kind or '').lower() == 'sound' else None
    if isinstance(component, aaf2.components.Filler):
        return [], [component], []
    if isinstance(component, aaf2.components.Sequence):
        if not len(component.components) or component.length != sequence_duration(component.components):
            return None
        child = component.components[0 if at_start else -1]
        result = extension_nodes(child, at_start)
        if result is None:
            return None
        sources, fillers, parents = result
        return sources, fillers, parents + [component]
    if not isinstance(component, aaf2.components.OperationGroup):
        return None
    inputs = list(component.segments)
    required = component.operation.number_inputs
    if (not inputs or component.operation['IsTimeWarp'].value or
            (required is not None and required > 0 and len(inputs) != required)):
        return None
    sources, fillers, parents = [], [], []
    for child in inputs:
        if child.length != component.length:
            return None
        result = extension_nodes(child, at_start)
        if result is None:
            return None
        child_sources, child_fillers, child_parents = result
        sources.extend(child_sources)
        fillers.extend(child_fillers)
        parents.extend(child_parents)
    return sources, fillers, parents + [component]


def source_bounds(source, edit_rate, visited=None):
    """Find verified bounds in the referenced slot's units; unknown is unsafe."""
    mob = source.mob
    if mob is None:
        return None
    visited = set() if visited is None else visited
    key = (str(mob.mob_id), source.slot_id)
    if key in visited:
        return None
    visited.add(key)
    try:
        slot = mob.slot_at(source.slot_id)
    except (KeyError, IndexError):
        return None
    if (not isinstance(slot, aaf2.mobslots.TimelineMobSlot) or
            slot.edit_rate != edit_rate or slot.origin != 0):
        return None
    segment = slot.segment
    if isinstance(mob, aaf2.mobs.SourceMob):
        descriptor = mob.descriptor
        length = getattr(descriptor, 'length', None)
        rate = descriptor['SampleRate'].value if descriptor and 'SampleRate' in descriptor else None
        if length is None or rate is None or rate <= 0:
            return None
        # FileDescriptor.Length uses its SampleRate, not the timeline edit rate.
        # Pro Tools commonly writes 48 kHz media into a 24000/1001 fps slot.
        if ('AudioSamplingRate' in descriptor and
                descriptor['AudioSamplingRate'].value != rate):
            return None
        duration = Fraction(length) * Fraction(edit_rate) / Fraction(rate)
        return 0, min(segment.length, duration)
    if not isinstance(segment, aaf2.components.SourceClip):
        return None
    bounds = source_bounds(segment, edit_rate, visited)
    if bounds is None:
        return None
    low, high = bounds
    return max(0, low - segment.start), min(segment.length, high - segment.start)


def is_rendered_fade(component):
    source, _ = source_chain(component)
    return bool(source and source.mob and FADE_NAME.fullmatch((source.mob.name or '').strip()))


def can_extend(component, amount, at_start, edit_rate):
    return extension_problem(component, amount, at_start, edit_rate) is None


def extension_problem(component, amount, at_start, edit_rate):
    if component is None or isinstance(component, aaf2.components.Filler):
        return None
    if component.length <= 0:
        return 'zero-length audible neighbor'
    nodes = extension_nodes(component, at_start)
    if nodes is None:
        return 'unsupported input structure or time warp'
    sources, _, groups = nodes
    for source in sources:
        if is_rendered_fade(source):
            return 'adjacent rendered fade; the relationship between consecutive fades is ambiguous'
        bounds = source_bounds(source, edit_rate)
        if bounds is None:
            return 'unverified source bounds, edit rates, or slot origin'
        low, high = bounds
        start = source.start - (amount if at_start else 0)
        end = source.start + source.length + (0 if at_start else amount)
        if not low <= start <= end <= high:
            available = source.start - low if at_start else high - source.start - source.length
            return f'insufficient handles: need {amount} units, available {available}'
    for group in groups:
        if not isinstance(group, aaf2.components.OperationGroup):
            continue
        for parameter in group.parameters:
            if hasattr(parameter, 'pointlist') and len(parameter.pointlist):
                if parameter.interpolationdef is None or parameter.interpolationdef.auid not in (
                        aaf2.misc.LinearInterp, aaf2.misc.ConstantInterp,
                        aaf2.misc.BezierInterpolator, aaf2.misc.CubicInterpolator):
                    return 'unknown automation interpolation'
                times = [point.time for point in parameter.pointlist]
                if any(not 0 <= time <= 1 for time in times) or any(a >= b for a, b in zip(times, times[1:])):
                    return 'invalid or unordered automation points'
    return None


def extend_component(f, component, amount, at_start):
    sources, fillers, parents = extension_nodes(component, at_start)
    for parent in parents:
        if isinstance(parent, aaf2.components.OperationGroup):
            scale_points(f, parent, parent.length, parent.length + amount,
                         amount if at_start else 0)
        parent.length += amount
    for filler in fillers:
        filler.length += amount
    for source in sources:
        if at_start:
            source.start -= amount
        source.length += amount


def sequence_duration(components):
    return sum(-c.length if isinstance(c, aaf2.components.Transition) else c.length
               for c in components)


def source_positions(components, position=0):
    """Keep references alive while checking sample anchors through effect trees."""
    for component in components:
        if isinstance(component, aaf2.components.Transition):
            position -= component.length
            continue
        if isinstance(component, aaf2.components.SourceClip):
            if not is_rendered_fade(component):
                yield id(component), (component, position, component.start, component.length)
        elif isinstance(component, aaf2.components.Sequence):
            yield from source_positions(component.components, position)
        elif isinstance(component, aaf2.components.OperationGroup):
            for child in component.segments:
                yield from source_positions([child], position)
        position += component.length

def scale_points(f, op_group, old_len, new_len, shift_forward=0):
    if 'Parameters' not in op_group: return
    for p in op_group['Parameters'].value:
        if hasattr(p, 'pointlist') and len(p.pointlist) > 0:
            pts = list(p.pointlist)
            for pt in pts:
                sample_pos = pt.time * old_len + shift_forward
                pt.time = sample_pos / new_len
                if p.interpolationdef.auid == aaf2.misc.BezierInterpolator:
                    for side, tangent in zip(('IN', 'OUT'), pt.tangents):
                        set_tangent(f, pt, side, (tangent[0] * old_len / new_len, tangent[1]))
            
            # Force points at boundaries to prevent unwanted ramping in Pro Tools
            # A cubic's endpoint tangents depend on its neighbors. Keep its point
            # topology unchanged; evaluation clamps outside its first/last point.
            cubic = p.interpolationdef.auid == aaf2.misc.CubicInterpolator
            if pts[0].time > 0 and not cubic:
                if p.interpolationdef.auid == aaf2.misc.BezierInterpolator:
                    set_tangent(f, pts[0], 'IN', (0, 0))
                new_pt = f.create.ControlPoint()
                new_pt.time = 0.0
                new_pt.value = pts[0].value
                if 'EditHint' in pts[0]: new_pt['EditHint'].value = pts[0]['EditHint'].value
                all_pts = [new_pt] + pts
                while len(p.pointlist) > 0: p.pointlist.pop(0)
                for pt in all_pts: p.pointlist.append(pt)
                pts = list(p.pointlist)
                
            if pts[-1].time < 1 and not cubic:
                if p.interpolationdef.auid == aaf2.misc.BezierInterpolator:
                    set_tangent(f, pts[-1], 'OUT', (0, 0))
                new_pt = f.create.ControlPoint()
                new_pt.time = 1.0
                new_pt.value = pts[-1].value
                if 'EditHint' in pts[-1]: new_pt['EditHint'].value = pts[-1]['EditHint'].value
                p.pointlist.append(new_pt)


def set_tangent(f, point, side, tangent):
    properties = point['ControlPointPointProperties']
    for axis, value in zip(('POS', 'VAL'), tangent):
        name = f'PP_{side}_TANGENT_{axis}_U'
        existing = next((prop for prop in properties if prop.name == name), None)
        rational = aaf2.rational.AAFRational(float(value))
        if existing is None:
            if value == 0:
                continue
            # Existing Bezier data includes the definitions for its nonzero
            # tangents. Never invent proprietary Avid parameter identifiers.
            parameter_def = next((d for d in f.dictionary['ParameterDefinitions'].values()
                                  if d.name == name), None)
            if parameter_def is None:
                raise ValueError(f'Missing Bezier tangent definition: {name}')
            properties.append(f.create.ConstantValue(parameter_def, rational))
        else:
            existing.value = rational


def process_fades(in_path, out_path, report_path=None, consecutive='auto', curve='auto', target='protools'):
    if os.path.realpath(in_path) == os.path.realpath(out_path) or (
            os.path.exists(out_path) and os.path.samefile(in_path, out_path)):
        raise ValueError('Input and output must be different files.')
    if consecutive not in ('auto', 'preserve', 'split', 'merge'):
        raise ValueError('Consecutive fade mode must be auto, preserve, split, or merge.')
    if curve not in ('auto', 'linear', 'equal-power', 'sine', 'sqrt-power'):
        raise ValueError('Unknown curve policy.')
    if target not in ('protools', 'generic'):
        raise ValueError('Target must be protools or generic.')
    if curve == 'equal-power':
        curve = 'sine'
    if target == 'protools' and curve == 'sqrt-power':
        raise ValueError('Use --target generic for a square-root power curve; Pro Tools requires native sine/cosine encoding.')
    if report_path and any(
            os.path.realpath(report_path) == os.path.realpath(path) or
            (os.path.exists(report_path) and os.path.exists(path) and os.path.samefile(report_path, path))
            for path in (in_path, out_path)):
        raise ValueError('The report must not overwrite the input or output AAF.')
    print(f"Copying {in_path} to {out_path} for processing...")
    output_dir = os.path.dirname(os.path.abspath(out_path))
    with tempfile.TemporaryDirectory(prefix='.aaf2aaf-', dir=output_dir) as workspace:
        working_path = os.path.join(workspace, 'working.aaf')
        shutil.copyfile(in_path, working_path)
        report = rewrite_fades(working_path, consecutive, curve, os.path.dirname(os.path.abspath(in_path)), target)
        os.replace(working_path, out_path)
    if report_path:
        with open(report_path, 'w', encoding='utf-8') as stream:
            json.dump(report, stream, indent=2)
    return report


def audio_sequences(segment, path=''):
    """Process inner sequences first so enclosing effects retain their structure."""
    if isinstance(segment, aaf2.components.Sequence):
        for i, child in enumerate(list(segment.components)):
            yield from audio_sequences(child, f'{path}/component:{i}')
        if (segment.media_kind or '').lower() == 'sound' and not (
                path and len(segment.components) == 1 and is_rendered_fade(segment)):
            yield path, segment
    elif isinstance(segment, aaf2.components.OperationGroup):
        for i, child in enumerate(segment.segments):
            yield from audio_sequences(child, f'{path}/input:{i}')


def rewrite_fades(path, consecutive='auto', curve='auto', source_directory=None, target='protools'):
    print("Un-rendering fades into virtual transitions...")
    report = {'target': target, 'converted': [], 'preserved': []}
    with aaf2.open(path, 'rw') as f:
        from aaf_audio import MediaReader
        reader = MediaReader(f, source_directory or os.path.dirname(os.path.abspath(path)))
        operation_id = aaf2.auid.AUID('0c3bea41-fc05-11d2-8a29-0050040ef7d2')
        op_def = f.dictionary.lookup_operationdef(operation_id)
        if op_def is None:
            op_def = f.create.OperationDef(operation_id, 'Audio Dissolve')
            op_def.media_kind = 'sound'
            op_def['NumberInputs'].value = 2
            f.dictionary.register_def(op_def)

        for mob in f.content.mobs:
            if not isinstance(mob, aaf2.mobs.CompositionMob): continue
            
            for slot in mob.slots:
                if not isinstance(slot, aaf2.mobslots.TimelineMobSlot):
                    continue
                for sequence_path, seq in audio_sequences(slot.segment):
                    convert_sequence(f, mob, slot, seq, sequence_path, op_def, report, consecutive, curve, reader, target)
    return report


def convert_sequence(f, mob, slot, seq, sequence_path, op_def, report, consecutive, curve, reader, target):
    comps = list(seq.components)
    original_duration = sequence_duration(comps)
    anchors = dict(source_positions(comps))
    new_comps = []
    i = 0
    while i < len(comps):
        c = comps[i]
        if not is_rendered_fade(c):
            new_comps.append(c)
            i += 1
            continue
        end = i + 1
        if consecutive != 'preserve':
            while end < len(comps) and is_rendered_fade(comps[end]):
                end += 1
        renders = comps[i:end]
        fade_len = sum(render.length for render in renders)
        prev_c = new_comps[-1] if new_comps else None
        next_c = comps[end] if end < len(comps) else None
        prev_silence = prev_c is None or isinstance(prev_c, aaf2.components.Filler)
        next_silence = next_c is None or isinstance(next_c, aaf2.components.Filler)
        mode = consecutive
        classification_problem = None
        if mode == 'auto' and len(renders) > 1:
            try:
                mode = classify_fade_run(reader, renders, prev_c, next_c, slot.edit_rate)
            except ValueError as error:
                classification_problem = str(error)
        split = mode == 'split' and len(renders) > 1
        prev_amount = renders[0].length if split else fade_len
        next_amount = renders[-1].length if split else fade_len
        event = {'mob': mob.name, 'slot': slot.slot_id, 'sequence': sequence_path,
                 'component': i, 'length': fade_len, 'render_count': len(renders),
                 'kind': 'split_fades' if split else 'fade_in' if prev_silence else
                         'fade_out' if next_silence else 'crossfade'}
        problem = (classification_problem or
                   ('no adjacent audible clip' if prev_silence and next_silence else
                   'nonpositive fade length' if any(render.length <= 0 for render in renders) else
                   'split mode requires exactly two renders between audible clips'
                   if split and (len(renders) != 2 or prev_silence or next_silence) else
                   extension_problem(prev_c, prev_amount, False, slot.edit_rate) or
                   extension_problem(next_c, next_amount, True, slot.edit_rate)))
        curves = [curve, curve] if split else [curve]
        if problem is None and curve == 'auto':
            from aaf_audio import infer_curve
            try:
                if split:
                    inferred = [infer_curve(reader, renders[0], prev_c, None, prev_amount, slot.edit_rate),
                                infer_curve(reader, renders[1], None, next_c, next_amount, slot.edit_rate)]
                else:
                    inferred = [infer_curve(reader, renders, prev_c, next_c, fade_len, slot.edit_rate)]
                curves = [name for name, _ in inferred]
                event['channels'] = reader.verified_channels
                event['curve_relative_rms_error'] = [error for _, error in inferred]
                if reader.verified_channels not in (1, 2):
                    problem = 'audio dissolves with more than two channels need an explicit channel layout'
                if target == 'protools' and 'sqrt-power' in curves:
                    problem = ('square-root power curve is not verified as portable to Pro Tools; '
                               'use the generic target for explicit multipoint encoding')
            except (ValueError, OSError, KeyError, IndexError) as error:
                problem = f'curve verification failed: {error}'
        event['curves'] = curves
        event['curve_source'] = ('verified against rendered audio' if problem is None else 'unverified') if curve == 'auto' else 'requested'
        if problem:
            report['preserved'].append(dict(event, reason=problem))
            warnings.warn(f'Keeping rendered fade in mob {mob.name!r}, slot {slot.slot_id}, '
                          f'component {i}: {problem}.', UserWarning)
            new_comps.extend(renders)
            i = end
            continue
        if prev_c is None:
            prev_c = f.create.Filler('sound', 0)
            new_comps.append(prev_c)
        if next_c is None:
            next_c = f.create.Filler('sound', 0)
            comps.append(next_c)
        extend_component(f, prev_c, prev_amount, False)
        extend_component(f, next_c, next_amount, True)
        transition_def = op_def
        if event.get('channels') == 2:
            stereo_id = aaf2.auid.AUID('0c3bea42-fc05-11d2-8a29-0050040ef7d2')
            transition_def = f.dictionary.lookup_operationdef(stereo_id)
            if transition_def is None:
                transition_def = f.create.OperationDef(stereo_id, 'StereoAudioDissolve')
                transition_def.media_kind = 'sound'
                transition_def.number_inputs = 2
                f.dictionary.register_def(transition_def)
        if split:
            new_comps.extend((make_transition(f, transition_def, prev_amount, prev_amount, curves[0], target),
                              f.create.Filler('sound', fade_len),
                              make_transition(f, transition_def, next_amount, 0, curves[1], target)))
        else:
            cut = 0 if prev_silence else fade_len if next_silence else fade_len // 2
            new_comps.append(make_transition(f, transition_def, fade_len, cut, curves[0], target))
        report['converted'].append(event)
        i = end
    if sequence_duration(new_comps) != original_duration:
        raise ValueError(f'Timeline duration changed in mob {mob.name!r}, slot {slot.slot_id}.')
    new_anchors = dict(source_positions(new_comps))
    for identity, (_, position, start, length) in anchors.items():
        if identity not in new_anchors:
            raise ValueError('A source clip disappeared during reconstruction.')
        source, new_position, _, _ = new_anchors[identity]
        if (new_position + start - source.start != position or source.start > start or
                source.start + source.length < start + length):
            raise ValueError('A source clip lost audio or changed its timeline position.')
    seq.length = original_duration
    seq.components.value = new_comps


def classify_fade_run(reader, renders, previous, following, edit_rate):
    """Only infer a consecutive-fade relationship when rendered audio proves it."""
    from aaf_audio import infer_curve
    matches = []
    total = sum(r.length for r in renders)
    if can_extend(previous, total, False, edit_rate) and can_extend(following, total, True, edit_rate):
        try:
            infer_curve(reader, renders, previous, following, total, edit_rate)
            matches.append('merge')
        except (ValueError, OSError, KeyError, IndexError):
            pass
    if len(renders) == 2 and previous is not None and following is not None:
        if (can_extend(previous, renders[0].length, False, edit_rate) and
                can_extend(following, renders[1].length, True, edit_rate)):
            try:
                infer_curve(reader, renders[0], previous, None, renders[0].length, edit_rate)
                infer_curve(reader, renders[1], None, following, renders[1].length, edit_rate)
                matches.append('split')
            except (ValueError, OSError, KeyError, IndexError):
                pass
    if len(matches) != 1:
        raise ValueError('consecutive fades are ambiguous; use --consecutive split or merge only when their relationship is known')
    return matches[0]


def make_transition(f, op_def, length, cut, curve='linear', target='protools'):
    transition = f.create.Transition('sound', length)
    transition['OperationGroup'].value = f.create.OperationGroup(op_def, length, 'sound')
    transition.cutpoint = cut
    if curve in ('linear', 'sqrt-power', 'sine'):
        param_id = aaf2.auid.AUID('e4962320-2267-11d3-8a4c-0050040ef7d2')
        parameter_def = f.dictionary.lookup_parameterdef(param_id)
        if parameter_def is None:
            parameter_def = f.create.ParameterDef(param_id, 'Level', '', 'Rational')
            f.dictionary.register_def(parameter_def)
        if not any(p.auid == param_id for p in op_def.parameters):
            op_def.parameters.append(parameter_def)
        # Write explicit levels even for linear fades: some importers otherwise
        # substitute their own default crossfade shape.
        native_power = curve == 'sine' and target == 'protools'
        interp_id = aaf2.auid.AUID('c09153f7-bd18-4e5a-ad09-cbdd654fa001') if native_power else aaf2.misc.LinearInterp
        interpolation = f.dictionary.lookup_interperlationdef(interp_id)
        if interpolation is None:
            interpolation = f.create.InterpolationDef(interp_id, 'PowerInterpolator' if native_power else 'LinearInterp')
            f.dictionary.register_def(interpolation)
        parameter = f.create.VaryingValue(parameter_def, interpolation)
        if curve == 'linear' or native_power:
            points = [(0, 0), (1, 1)]
        elif curve == 'sine':
            points = [(i/1024, math.sin(math.pi*i/2048)) for i in range(1025)]
        else:
            points = [(i**4 / 1024**4, (i/1024)**2) for i in range(1025)]
        last = -1
        for time, value in points:
            encoded_time = aaf2.rational.AAFRational(time)
            if float(encoded_time) <= last:
                continue
            parameter.add_keyframe(encoded_time, aaf2.rational.AAFRational(value))
            last = float(encoded_time)
        transition['OperationGroup'].value.parameters.append(parameter)
    return transition

def unrender_fades(in_path, out_path, **kwargs):
    return process_fades(in_path, out_path, **kwargs)

def main():
    parser = argparse.ArgumentParser(description="AAF2AAF: Pro Tools Fade Un-Renderer")
    parser.add_argument("input_aaf", help="Path to the input .aaf file from Pro Tools")
    parser.add_argument("output_aaf", help="Path to the output .aaf file to generate")
    parser.add_argument('--report', help='Write a JSON report with converted fades and preservation reasons')
    parser.add_argument('--strict', action='store_true', help='Return a nonzero status if any detected fade stays rendered')
    parser.add_argument('--consecutive', choices=('auto', 'preserve', 'split', 'merge'), default='auto',
                        help='Interpret consecutive renders explicitly: separate fade-out/in or one crossfade')
    parser.add_argument('--curve', choices=('linear', 'equal-power', 'sine', 'sqrt-power', 'auto'), default='auto',
                        help='Verify linear, square-root power, or sine/cosine curves; or request a curve explicitly')
    parser.add_argument('--target', choices=('protools', 'generic'), default='protools',
                        help='Use native Pro Tools curve encoding, or generic multipoint curves')
    
    args = parser.parse_args()
    
    if not os.path.isfile(args.input_aaf):
        parser.error(f"Input file '{args.input_aaf}' does not exist or is not a file.")
        
    try:
        report = process_fades(args.input_aaf, args.output_aaf, args.report, args.consecutive, args.curve, args.target)
    except (OSError, ValueError) as error:
        parser.exit(1, f'Error: {error}\n')
    print(f"Converted renders: {sum(e['render_count'] for e in report['converted'])}; "
          f"kept rendered: {sum(e['render_count'] for e in report['preserved'])}.")
    print(f"Successfully generated un-rendered AAF: {args.output_aaf}")
    if args.strict and report['preserved']:
        parser.exit(1, 'Some fades remain rendered; see the warnings or JSON report.\n')

if __name__ == "__main__":
    main()
