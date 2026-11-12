# Scan previews and full-capture images

Use the [official RTX Viewer skill](https://github.com/NVIDIA/skills/blob/main/skills/omniverse-realtime-viewer/SKILL.md) for the live renderer and delivery shell. This package's additional tools consume recorded scan files independently of a viewer or the workbench.

## Inputs and dependencies

`runtime/scan_visuals.py` reads a capture directory containing:

- `capture.json`: `source_revision`, and `stations` with `id` and `points`.
- `labels.json`: indexed array with `ifc_guid`, `ifc_class`, and optional source revision.
- `<station-id>.ply`: the pipeline's full-resolution binary little-endian point export. Exact scalar properties: float x/y/z/intensity followed by uint ifc_label. Other PLY layouts must be converted explicitly; this tool is not a general PLY importer.

Coordinates must already share a world frame, in metres and Z-up for the cutaway option. Points, source USD and label tables must refer to the same revision. Captures with overlapping scanner views contain repeated surface observations; input count is not a count of unique surfaces.

For preview only, NumPy is sufficient. For offline images, install `requirements-visualization.txt` in an appropriate separate environment. It supplies NumPy, Warp and Pillow. CUDA is the default; `--device cpu` is available for small validation fixtures, not a recommended billion-point workload. There is no ARM-only restriction or dependency on the workbench, Isaac Sim, OVRTX, a browser, fonts or local material libraries for these two export commands. New scans still require the separate Isaac prerequisites.

## Bounded preview

```bash
python runtime/scan_visuals.py preview --run path/to/capture --output preview --budget 400000
```

Output: `preview.npz` (`points`, `label_index`), matching `labels.json`, and `preview.json` recording full/displayed counts, fraction, stride and source revision. Array coordinates are original recorded returns; labels are preserved. The tool visits the full input in bounded chunks and keeps every nth point across concatenated station order, independent of chunk size. This is deliberately a simple portable preview, not spatially uniform sampling, per-element coverage preservation or view-dependent level of detail. It replaces the reference application's two-stage, per-station preview preparation with an explicit single-budget export.

A consumer can create USD Points or its own point buffers from these arrays. Display the sampled count and percentage. Increasing point width does not increase scan density or accuracy. Sparse previews cannot establish that the underlying capture has gaps; expose a full-resolution export or offline view. A production interactive viewer can implement spatial tiling/progressive detail; that is not implemented in this package.

## Full-capture point images

```bash
python runtime/scan_visuals.py render --run path/to/capture --views examples/scan-views.json --output images
```

On Windows use the configured Python executable in PowerShell and Windows paths; the CLI arguments are identical. Adapt the example cameras to the actual asset. Each view has a unique `name`, world-space `eye` and `target`, an `up` vector, and `vertical_span_m` (orthographic frame height). Optional `clip_z_max` creates an explicitly labelled cutaway. `--width`, `--height`, `--chunk-size` and `--device` are configurable.

Outputs: one PNG per view plus `render.json` containing input/processed counts, resolution, cameras, clipping, occupied pixels and elapsed time. Every input return is processed; the entire capture need not reside in GPU memory. Working GPU memory scales with point chunk and number of image pixels/views. Multiple points can project to the same pixel; a depth buffer retains the nearest point. The depth key has 10-micrometre quantization and a 42,949 m camera-depth limit; this is a visualization limit, not a claim of sensor precision. Frustum/depth rejection and cutaways apply after points are read.

This is a **Warp point-cloud rasterizer of recorded RTX sensor output**, not an OVRTX path-traced render or a replacement renderer. Use OVRTX/its official skills for scene rendering, materials, lighting and generic image export. The specific addition here is chunked all-return projection with IFC class colouring, explicit cameras and an accounting record. No mesh filling, interpolation, synthetic returns or new captures are introduced. Colours encode IFC class, with distance shading; they are not RGB sensor measurements. Gaps remain gaps at the output resolution, although dense projections can appear solid.

## Present evidence distinctly

- Points: actual captured returns (sampled in a preview, all processed for full-capture images).
- Surface coverage: independent sampled surface checks, with covered/visible-gap/not-visible states. Not captured points or scanner positions.
- Scanner positions: sensor origins; useful context, not coverage evidence by themselves.

Choose whole-asset, opposing exterior and explicitly labelled interior/cutaway views. Keep the full source extent in at least one view, including terrain. Attractive full-scan images support visual review but do not replace numerical coverage or physical scanner calibration. Keep generated customer images and data local unless separately authorized to publish.
