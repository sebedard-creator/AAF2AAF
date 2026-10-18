# AAF2AAF: Pro Tools Fade Un-Renderer

[Changelog](CHANGELOG.md) · [Validation results](VALIDATION.md)

## Description

**AAF2AAF** is a standalone Python command-line tool (CLI) that reconstructs editable AAF transitions from supported rendered Pro Tools fades.

Pro Tools AAF exports can contain fades rendered as separate audio files (for example, "Fade 1" or "Fade 2"). Those rendered clips do not retain the editable fade controls needed to adjust their duration or curve after import.

**AAF2AAF acts as a temporal "Un-Renderer":**
It scans the timeline of an AAF exported by Pro Tools, detects rendered audio files that correspond to fades, calculates the handles of adjacent audio clips, and mathematically replaces those renders with genuine **standard AAF Transitions (Audio Dissolves)**.

Supported fades are replaced with standard AAF audio transitions. Fade-in and fade-out transitions use a cut point at the audible clip boundary (respectively zero and the fade length). In the default mode, the rendered audio is checked against the neighboring sources before selecting a curve. Pro Tools import and re-export have been verified on the five-fade test described in [VALIDATION.md](VALIDATION.md): the fades remain editable, the clips remain intact, and all five re-rendered fades are sample-identical to the native export.

## Prerequisites

- Python 3.8 or later
- `pyaaf2` and NumPy:

```bash
pip install -r requirements.txt
```

## Usage

Run the script directly from the command line; no graphical interface is required.

```bash
python AAF2AAF.py <input.aaf> <output.aaf>
```

**Example:**

```bash
python AAF2AAF.py my_protools_export.aaf editable_session.aaf
```

For a conversion report and a failure status when any detected fade remains rendered:

```bash
python AAF2AAF.py input.aaf output.aaf --report conversion.json --strict
```

The default settings are `--curve auto --target protools --consecutive auto`. Input and output must be different files. Existing embedded media stays embedded, and external media stays externally referenced; keep the source WAVE files available when importing the result.

If you are upgrading from the original script, automatic mode now preserves a rendered fade when its curve cannot be verified, even if its name matches. Inspect the printed conversion counts or use `--report` to see the reason. Use `--curve linear` or `--curve equal-power` only when you deliberately want that curve or know the intended original curve.

| Option | Behavior |
| --- | --- |
| `--curve auto` | Default: verify linear, sine/cosine, or square-root power models against the rendered PCM. A match must have a relative RMS error of at most 0.02% and be distinguishable from the other models. The selected target must also support its encoding. |
| `--curve linear` | Explicitly request equal-amplitude linear fades, without claiming recovery of the original curve. |
| `--curve equal-power` | Explicitly request a sine/cosine constant-power curve; `sine` is an alias. |
| `--target protools` | Default: explicit linear levels or native AAF Power interpolation for sine/cosine crossfades. Unverified square-root curves remain rendered. |
| `--target generic` | Encode nonlinear curves as multipoint linear parameters. Other DAWs require their own import validation. Also permits `--curve sqrt-power`. |
| `--consecutive auto` | Default: distinguish a split crossfade from adjacent fade-out/fade-in renders by testing their audio. Ambiguous runs remain rendered. |
| `--consecutive split` / `merge` | Explicitly select the relationship of consecutive renders when it is known. Source bounds are still enforced. |
| `--consecutive preserve` | Leave consecutive rendered fades unchanged. |
| `--report path.json` | Record each converted or preserved fade, its curve policy, and any preservation reason. |
| `--strict` | Return a nonzero exit status when a detected fade remains rendered. A valid output AAF is still produced. |

### Reports and exit status

The JSON report contains the selected `target` and two lists: `converted` and `preserved`. Entries identify the composition, slot, sequence, original component, fade length, and curve policy. Preserved entries include a `reason`. Automatically verified entries also record the fitted curve, channel count, and relative RMS error. `render_count` counts original rendered clips: a merged or split run can be represented by one report entry.

| Exit status | Meaning |
| --- | --- |
| `0` | Conversion succeeded. Without `--strict`, some detected fades may remain rendered; check the counts or report. |
| `1` | A conversion or file operation failed, or `--strict` found a detected fade that stayed rendered. |
| `2` | Command-line arguments or the input-file argument are invalid. |

### Exporting a test from Pro Tools

Use fade-in only, fade-out only, both fades on one clip, and a crossfade as separate cases. Include the media and unrendered handles at least as long as each fade. Short fades of 100–500 ms with one-second handles are suitable for a first test; the duration does not need to be exact.

The handle length is an export setting for additional source audio before and after clips. Increasing the fade duration does not increase the available handles. Import the converted AAF into a new session, retain absolute positions, and verify editable fades, intact clips, and audio against the source session.

## Features

- **Conservative name detection**: Recognizes names such as `Fade`, `Fade 1`, and `Fade 1.L.wav`, case-insensitively, including Pro Tools' trailing spaces. This remains a naming heuristic, not proof that a clip is a rendered fade. Other names are left unchanged.
- **Crossfade reconstruction**: Extends neighboring audio `SourceClip` objects, including compatible operation groups with multiple inputs and nested sequences, to recreate the overlap. Mono and stereo dissolve definitions are selected from verified channel layouts.
- **Automation preservation**: Retains linear, constant, Bezier, and cubic automation. Bezier time tangents are rescaled; cubic point neighborhoods remain unchanged so their original curve is preserved.
- **Fade-in / fade-out reconstruction**: Extends the audible clip over the whole rendered fade and overlaps it with silence, keeping the original timeline positions. Handles must cover the entire fade.
- **Verified handles**: Checks the referenced slot, source offsets, descriptor length, and edit rates, converting media sample counts into timeline units with exact rational arithmetic. Unsupported structures, unverified bounds, and insufficient handles leave the original rendered fade intact and produce a warning. Fades are never shortened to fit.
- **Timeline preservation**: Checks the effective sequence duration and every retained source clip's sample anchor and original audio coverage. Picture sequences are left unchanged.
- **Output protection**: Works on a temporary copy and replaces the output AAF only after conversion succeeds. A failure during conversion leaves the original input and any previous output AAF intact.
- **Curve preservation**: Verifies supported curve families from embedded PCM or external WAVE media. Writes explicit levels, since importer defaults can change crossfade curves. Pro Tools' native constant-power interpolation was verified by re-exporting and comparing the audio.

## Information limits

Missing source samples cannot be reconstructed from a rendered fade alone. Silence cannot identify a fade curve, and arbitrary crossfades do not uniquely identify two independent gain functions. The default mode preserves such renders and reports why. Known curve overrides are available when that information is supplied by the user.

Time warps, unresolved source chains, incompatible source slot rates or origins, unknown channel layouts, compressed or unreadable media, and effects whose audio cannot be evaluated remain rendered in automatic mode. Generic multipoint curves are not used for the Pro Tools target: the tested importer flattened them to linear curves. These are explicit preservation cases, not silently shortened or guessed fades.

When exporting from Pro Tools, include unrendered media handles at least as long as the fades you want to reconstruct. For example, a 15-second fade cannot be reconstructed from a neighboring clip with only one second of handles; export longer handles or use shorter test fades.

## Tests

```bash
python -B -m unittest -v
```

The regression suite generates temporary AAFs with embedded PCM audio. It checks CLI execution and strict reporting, mono/stereo crossfades, source bounds, consecutive fades, nested and multiple-input effects, automation, curve inference, and output protection after failure. See [VALIDATION.md](VALIDATION.md) for the manual Pro Tools round trip.

Manual test sessions, media, and diagnostic scripts specific to those sessions are kept outside the repository. See [VALIDATION.md](VALIDATION.md) for the native export/import/re-export procedure and tested dependency versions.

## License

[MIT](LICENSE).

Conçu par Sébastien Bédard
