# Changelog

Changes are recorded here before a release is tagged. This update has not been assigned a version number.

## Unreleased

### Fixed

- Fix single-sided fade-in/fade-out reconstruction so imported Pro Tools clips remain intact with editable fades. Place transition cut points at the audible clip boundary: zero for fade-in and the full fade length for fade-out. Addresses [issue #1](https://github.com/sebedard-creator/AAF2AAF/issues/1).
- Remove the duplicate CLI entry point that ran the entire conversion twice.
- Prevent timeline shortening when available handles are shorter than a rendered fade. Preserve the original render and report the reason instead of truncating the transition or creating a zero-length transition.
- Extend direct `SourceClip` neighbors as well as compatible effect groups and nested sequences.
- Check the referenced source slot, offsets, descriptor length, and edit rates. Convert media sample counts to timeline units using rational arithmetic, including 48 kHz audio on a 24000/1001 fps timeline. Unknown bounds no longer imply unlimited handles.
- Recognize Pro Tools' `Fade ` names with trailing spaces, match supported names case-insensitively, and avoid matching arbitrary clip names merely containing “Fade”.
- Write explicit transition levels and native AAF Power interpolation for Pro Tools sine/cosine crossfades. This avoids importer defaults changing curves or flattening nonlinear multipoint curves to linear.
- Preserve automation timing and supported curve shapes when extending clips, including Bezier time tangents and cubic point neighborhoods.
- Check sequence duration, retained source positions, and original audio coverage after reconstruction. Leave picture sequences unchanged.
- Protect the input and previous output AAF by converting a temporary copy and replacing the output only after successful conversion. Reject input/output aliases and reports that would overwrite an AAF.

### Added

- PCM-based recognition of linear, sine/cosine, and square-root power curve families, with reported fit errors and target-specific encoding checks.
- Automatic disambiguation of supported consecutive fade renders, plus explicit `split`, `merge`, and `preserve` policies.
- Compatible multiple-input effect handling, nested audio sequence traversal, and mono/stereo dissolve selection from verified channel layouts.
- JSON conversion reports with per-fade results and preservation reasons, printed render counts, and `--strict` status reporting.
- `--curve`, `--target`, and `--consecutive` options for explicit conversion policies.
- A 24-test regression suite and a documented native Pro Tools export/import/re-export validation procedure.

### Changed

- Default conversion now verifies the curve against available rendered PCM and targets Pro Tools. Silent, unidentified, unsupported, or insufficiently sourced fades stay rendered and are reported. Explicit curve options remain available when the intended curve is known.
- Add NumPy (`numpy>=1.24`) alongside `pyaaf2`; install dependencies with `pip install -r requirements.txt`.
- Document media requirements, export handles, target compatibility, exit statuses, and information limits in [README.md](README.md).
- Keep session-specific diagnostic scripts and media outside the repository; ignore Python bytecode and the local `.venv` directory.

### Validation

Validated on October 18, 2026, with a native Pro Tools AAF at 48 kHz, 24-bit mono PCM, 24000/1001 fps, and five 12-frame fades. The imported fades remained editable and the clips remained intact. After the final Pro Tools re-export, all five fade renders were identical sample for sample to the native conversion input, with zero RMS difference. Stereo and more complex effect configurations have automated structural coverage; equivalent Pro Tools round-trip coverage is not claimed. See [VALIDATION.md](VALIDATION.md).

### Preservation cases

Missing samples, indeterminate or unsupported curves, time warps, unresolved source chains, incompatible rates or origins, unreadable media, and unsupported channel layouts remain explicit preservation cases. The converter does not fabricate missing audio or claim exact recovery from ambiguous renders.
