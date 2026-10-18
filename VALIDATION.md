# Validation

This document records the tested configuration and results for the single-sided fade slicing bug in [issue #1](https://github.com/sebedard-creator/AAF2AAF/issues/1). The corresponding changes are listed under **Unreleased** in [CHANGELOG.md](CHANGELOG.md).

## Test environment

| Component | Tested version or configuration |
| --- | --- |
| Converter runtime | Python 3.11.9 on Windows |
| AAF library | pyaaf2 1.7.1 |
| Audio analysis | NumPy 2.2.6 |
| Pro Tools | User-operated; version not recorded |
| Audio | 48 kHz, 24-bit mono PCM |
| Timeline | 24000/1001 fps |
| Fade length | 12 frames / 0.5005 seconds / 24,024 samples |
| Export handles | One second |

## Pro Tools round trip — October 18, 2026

The user imported a diagnostic with five short fades into Pro Tools and confirmed that the fades were editable and the clips remained intact. They then exported that session as a native Pro Tools AAF with media. That native export was converted and imported again, and the user confirmed editable fades, intact clips, and conforming sound.

The native export used 48 kHz, 24-bit mono PCM, a 24000/1001 fps timeline, one-second handles, and five 12-frame fades (0.5005 seconds, 24,024 audio samples each):

| Case | Original render | Final re-export comparison |
| --- | --- | --- |
| Fade-in only | Linear | Sample-identical |
| Fade-out only | Linear | Sample-identical |
| Fade-in on a clip with both fades | Linear | Sample-identical |
| Fade-out on that same clip | Linear | Sample-identical |
| Crossfade | Sine/cosine constant power | Sample-identical |

The five fade positions and lengths also matched. The final converter's transition definitions, cut points, interpolation identifiers, and control points were checked against the manually tested variant. The Pro Tools version was not recorded.

The comparison covered 120,120 decoded mono PCM samples across the five fade renders. Every sample matched, and the RMS difference was zero for each fade. This compares audio samples, not whole WAVE or AAF files, whose metadata can differ.

Two importer behaviors were discovered during this test:

- Omitting the level parameter lets Pro Tools substitute a default crossfade shape.
- A nonlinear curve represented as many linearly interpolated level points was flattened to linear by the tested Pro Tools importer. Native AAF Power interpolation preserved its sine/cosine crossfade. The re-exported PCM for every fade was identical to the original native export, with zero RMS difference.

This validates the mono configuration above. Stereo selection and more complex effect trees are covered structurally by automated tests, and require their own application-level samples before claiming equivalent Pro Tools coverage.

## Reproducing the manual validation

1. Prepare the four ordered cases: fade-in only, fade-out only, both on one clip, and a crossfade. Use audible sources and sufficient handles. Alternatively, generate the diagnostic from a matching native test export with `tools/create_protools_diagnostic.py`, as described in [README.md](README.md).
2. Import the diagnostic into Pro Tools and export it as a native AAF with media and one-second handles, retaining the short fades. This native export is the conversion input.
3. Run `python AAF2AAF.py native_export.aaf converted.aaf --report conversion.json --strict`. For this fixture, expect five converted renders, zero preserved renders, and exit status `0`.
4. Import `converted.aaf` into a new Pro Tools session with the matching audio and timeline rates. Retain absolute positions. Check that all five fades can be edited and that the clip with both fades remains intact.
5. Re-export that session with its media. Compare each rendered fade's decoded PCM, timeline position, and duration with the native export from step 2.

The test sessions, media, and machine-specific JSON comparison reports are stored separately from this repository. The automated suite generates its own temporary fixtures.

## Automated regressions

Run `python -B -m unittest -v`. The 24 test methods cover:

- One CLI conversion per invocation, invalid input, strict status, and JSON reasons.
- Direct clips, nested gain groups and sequences, and compatible multiple-input mixers.
- Fade-in/out cut points, both fades on one clip, crossfade overlap, and mono/stereo operation selection.
- Exact sequence duration, source position anchors, and original audio coverage.
- Insufficient, missing, inconsistent, and unverified media bounds without truncating fades.
- Linear, constant, Bezier, and cubic automation position/shape preservation.
- Audio-based recognition of linear, square-root power, and sine/cosine fades; rejection of unknown and silent curves.
- Consecutive split/merged renders, inferred from audible data or selected explicitly.
- Atomic AAF replacement and preservation of a previous output on failure.

The earlier long-fade exports had only one second of handles and silent source media. They were checked as preservation cases, not presented as successful reconstructions.
