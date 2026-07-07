import os
import argparse
import shutil
import aaf2

def get_max_len(mob):
    if isinstance(mob, aaf2.mobs.MasterMob):
        for slot in mob.slots:
            if isinstance(slot.segment, aaf2.components.SourceClip):
                smob = slot.segment.mob
                if smob and hasattr(smob, 'descriptor') and hasattr(smob.descriptor, 'length'):
                    return smob.descriptor.length
    elif hasattr(mob, 'descriptor') and hasattr(mob.descriptor, 'length'):
        return mob.descriptor.length
    return float('inf')

def scale_points(f, op_group, old_len, new_len, shift_forward=0):
    if 'Parameters' not in op_group: return
    for p in op_group['Parameters'].value:
        if hasattr(p, 'pointlist') and len(p.pointlist) > 0:
            pts = list(p.pointlist)
            for pt in pts:
                sample_pos = pt.time * old_len + shift_forward
                pt.time = sample_pos / new_len
            
            # Force points at boundaries to prevent unwanted ramping in Pro Tools
            if pts[0].time > 0.0001:
                new_pt = f.create.ControlPoint()
                new_pt.time = 0.0
                new_pt.value = pts[0].value
                if hasattr(pts[0], 'edit_hint'): new_pt.edit_hint = pts[0].edit_hint
                all_pts = [new_pt] + pts
                while len(p.pointlist) > 0: p.pointlist.pop(0)
                for pt in all_pts: p.pointlist.append(pt)
                pts = list(p.pointlist)
                
            if pts[-1].time < 0.9999:
                new_pt = f.create.ControlPoint()
                new_pt.time = 1.0
                new_pt.value = pts[-1].value
                if hasattr(pts[-1], 'edit_hint'): new_pt.edit_hint = pts[-1].edit_hint
                p.pointlist.append(new_pt)


def process_fades(in_path, out_path):
    print(f"Copying {in_path} to {out_path} for processing...")
    shutil.copy(in_path, out_path)
    
    print("Un-rendering fades into virtual transitions...")
    with aaf2.open(out_path, 'rw') as f:
        try:
            op_def = f.create.OperationDef(aaf2.auid.AUID('0c3bea41-fc05-11d2-8a29-0050040ef7d2'), 'Audio Dissolve')
            op_def.media_kind = 'sound'
            op_def['NumberInputs'].value = 2
            f.dictionary.register_def(op_def)
        except:
            op_def = f.dictionary.lookup_operationdef('Audio Dissolve')

        for mob in f.content.mobs:
            if not isinstance(mob, aaf2.mobs.CompositionMob): continue
            
            for slot in mob.slots:
                if not isinstance(slot.segment, aaf2.components.Sequence): continue
                seq = slot.segment
                
                comps = list(seq.components)
                new_comps = []
                
                i = 0
                while i < len(comps):
                    c = comps[i]
                    is_fade = False
                    fade_len = c.length
                    
                    if isinstance(c, aaf2.components.OperationGroup):
                        for seg in c.segments:
                            if isinstance(seg, aaf2.components.SourceClip) and seg.mob and 'Fade' in seg.mob.name:
                                is_fade = True
                                break
                    elif isinstance(c, aaf2.components.SourceClip) and c.mob and 'Fade' in c.mob.name:
                        is_fade = True
                        
                    if is_fade:
                        prev_c = new_comps[-1] if new_comps else None
                        next_c = comps[i+1] if i+1 < len(comps) else None
                        
                        if not prev_c:
                            prev_c = f.create.Filler('sound', 0)
                            new_comps.append(prev_c)
                            
                        prev_handle = fade_len
                        next_handle = fade_len
                        
                        if isinstance(prev_c, aaf2.components.OperationGroup):
                            src = prev_c.segments[0]
                            max_len = get_max_len(src.mob)
                            avail = max_len - (src.start + src.length)
                            prev_handle = min(fade_len, avail)
                            
                        if next_c and isinstance(next_c, aaf2.components.OperationGroup):
                            src = next_c.segments[0]
                            avail = src.start
                            next_handle = min(fade_len, avail)
                        elif not next_c:
                            next_c = f.create.Filler('sound', 0)
                            comps.append(next_c)
                            next_handle = fade_len
                            
                        actual_L = min(fade_len, prev_handle, next_handle)
                        
                        if isinstance(prev_c, aaf2.components.Filler):
                            prev_c.length += fade_len
                        elif isinstance(prev_c, aaf2.components.OperationGroup):
                            src = prev_c.segments[0]
                            scale_points(f, prev_c, prev_c.length, prev_c.length + actual_L, 0)
                            src.length += actual_L
                            prev_c.length += actual_L
                            
                        if isinstance(next_c, aaf2.components.Filler):
                            next_c.length += fade_len
                        elif isinstance(next_c, aaf2.components.OperationGroup):
                            src = next_c.segments[0]
                            scale_points(f, next_c, next_c.length, next_c.length + actual_L, actual_L)
                            src.start -= actual_L
                            src.length += actual_L
                            next_c.length += actual_L
                            
                        trans = f.create.Transition('sound', actual_L)
                        og = f.create.OperationGroup(op_def)
                        og.media_kind = 'sound'
                        og.length = actual_L
                        trans['OperationGroup'].value = og
                        trans['CutPoint'].value = actual_L // 2
                        
                        new_comps.append(trans)
                    else:
                        new_comps.append(c)
                        
                    i += 1
                    
                while len(seq.components) > 0:
                    seq.components.pop(0)
                    
                for c in new_comps:
                    seq.components.append(c)

def main():
    parser = argparse.ArgumentParser(description="OttoAlign Un-Renderer (AAF-to-AAF)")
    parser.add_argument("input_aaf", help="Path to the input .aaf file")
    parser.add_argument("output_aaf", help="Path to the output .aaf file")
    
    args = parser.parse_args()
    
    process_fades(args.input_aaf, args.output_aaf)
    print(f"Successfully generated un-rendered AAF: {args.output_aaf}")

if __name__ == "__main__":
    main()


def unrender_fades(in_path, out_path):
    process_fades(in_path, out_path)

def main():
    import argparse
    import os
    parser = argparse.ArgumentParser(description="AAF2AAF: Pro Tools Fade Un-Renderer")
    parser.add_argument("input_aaf", help="Path to the input .aaf file from Pro Tools")
    parser.add_argument("output_aaf", help="Path to the output .aaf file to generate")
    
    args = parser.parse_args()
    
    if not os.path.exists(args.input_aaf):
        print(f"Error: Input file '{args.input_aaf}' does not exist.")
        return
        
    process_fades(args.input_aaf, args.output_aaf)
    print(f"Successfully generated un-rendered AAF: {args.output_aaf}")

if __name__ == "__main__":
    main()
