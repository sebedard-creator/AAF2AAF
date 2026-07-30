# AAF2AAF: Pro Tools Fade Un-Renderer

## Description

**AAF2AAF** is a standalone Python command-line tool (CLI).
It solves a major issue with Pro Tools AAF exports: virtual fades are rendered destructively.

When Pro Tools exports an AAF, it consolidates fades and renders them as actual audio files (for example, "Fade 1" or "Fade 2"). This prevents any subsequent editing—such as trimming or adjusting the fade curve—in another application or after re-importing the AAF.

**AAF2AAF acts as a temporal "Un-Renderer":**
It scans the timeline of an AAF exported by Pro Tools, detects rendered audio files that correspond to fades, calculates the handles of adjacent audio clips, and mathematically replaces those renders with genuine **standard AAF Transitions (Audio Dissolves)**.

The result is a clean, Media Composer-style AAF that makes Pro Tools (and other DAWs) regenerate real, editable, non-destructive virtual fades when imported.

## Prerequisites

- Python 3.x
- The `pyaaf2` library (`pip install pyaaf2`)

## Usage

Run the script directly from the command line; no graphical interface is required.

```bash
python AAF2AAF.py <input.aaf> <output.aaf>
```

**Example:**

```bash
python AAF2AAF.py my_protools_export.aaf editable_session.aaf
```

## Features

- **Heuristic detection**: Automatically identifies clips named "Fade..."
- **Accurate crossfade reconstruction**: Extends adjacent clips to recreate the overlap.
- **Fade-in / fade-out restoration**: Manipulates fillers (silence) to recreate opening and closing fades.
- **Strict handle compliance**: Never generates virtual fades longer than the audio actually available in the consolidated source files, preventing "out of bounds" import errors.
- **Desynchronization correction**: Preserves the timeline's total duration, even when fades are truncated because of insufficient handles.

Conçu par Sébastien Bédard
