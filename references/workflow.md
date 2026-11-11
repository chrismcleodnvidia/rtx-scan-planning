# Runtime commands

Complete [setup and preflight](../README.md) first. The examples below use Bash. In PowerShell, replace `"$BIMSCAN_PYTHON"` with `& $env:BIMSCAN_PYTHON`; all CLI subcommands and arguments are the same. Set the data path before changing into `runtime/`.

Run commands from the configured workbench or the sharing package's `runtime/` directory. Use a local working disk for caches and captures. The Python environment needs NumPy, SciPy, Warp and a compatible `pxr` installation. Isaac Sim provides the native RTX sensor process. The NVIDIA CAD Converter is needed only when importing a new IFC file.

Configure paths for the actual installation. `BIMSCAN_PYTHON` is the planning/measurement Python; `ISAAC_SIM_PYTHON` is Isaac Sim's launcher. `BIMSCAN_CAD_PYTHON` selects the separate CAD-converter environment. `BIMSCAN_DATA` optionally selects a data directory outside the runtime. Do not install a second OpenUSD provider into an existing NVIDIA environment just to satisfy an import.

The tested capture implementation uses `isaacsim.sensors.rtx.LidarRtx`, GMO return data and native stable IDs. Its current reference run used Isaac Sim 5.1. Check the installed API and run a small native capture before assuming compatibility with another release. Planning uses Warp mesh queries; capture uses RTX in Isaac Sim. OVStage/OVRTX are optional for the downstream viewer, not required by the command-line scan workflow.

The tested planning/measurement environment used Python 3.12.3, NumPy 2.5.3, SciPy 1.16.1, Warp 1.17.0 and OpenUSD 25.05 on Linux aarch64. Isaac Sim runs in its own environment. These identify the tested configuration, not a requirement to replace packages in another supported NVIDIA installation.

## Initial scan

```bash
"$BIMSCAN_PYTHON" pipeline.py import /path/to/building.ifc --name Building
# Use the dataset identifier printed by import.
"$BIMSCAN_PYTHON" pipeline.py plan DATASET --profile precision --exterior --elevated
"$BIMSCAN_PYTHON" pipeline.py scan DATASET --profile precision
```

The standard converter owns IFC-to-USD conversion. Scan preparation retains that output and creates a metre/Z-up scan composition and geometry caches. IFC metadata is read from native USD. The planner can use a reviewed `scan-floor-selection.json` when a source labels ground or stairs unusually; this is a placement hint, not a reclassification or geometry edit.

`pipeline.py survey DATASET --run RUN` produces the existing survey report. A supplied survey brief defines scope and criteria. Review the actual exported geometry and scanner assumptions before treating the result as a field-survey plan.

## Density refinement

Set unique output directories and pin the baseline run explicitly:

```bash
"$BIMSCAN_PYTHON" density_plan.py DATASET --base-run BASE_RUN --output WORK/round1
"$BIMSCAN_PYTHON" scan_refinement.py capture WORK/round1/plan.json \
  --run density-r1-a --state-dir WORK/state --start 0 --stop 40 --dry-run
"$BIMSCAN_PYTHON" scan_refinement.py capture WORK/round1/plan.json \
  --run density-r1-a --state-dir WORK/state --start 0 --stop 40
"$BIMSCAN_PYTHON" scan_refinement.py capture WORK/round1/plan.json \
  --run density-r1-b --state-dir WORK/state --start 40
```

The slice is an optional batch boundary, not a total scan limit. Check the plan's position count first; the example uses two batches only when more than 40 positions were selected. Capture every selected position across disjoint batches. Omitting `--stop` captures the rest of the plan. Repeating the same command resumes its recorded run. The helper checks the retained source/profile, creates run-qualified station IDs, invokes the existing native capture pipeline and writes completion state. It does not publish batches concurrently.

After all selected batches complete:

```bash
"$BIMSCAN_PYTHON" scan_refinement.py compare DATASET BASE_RUN density-r1-a density-r1-b \
  --output WORK/round1-measured.json
```

The comparison combines the retained coverage arrays, without re-capturing the baseline. It reports support, visible-area changes and per-IFC-element gaps. Listed run names must be distinct and must not include a combined run alongside its constituent captures.

For stubborn gaps, check first-hit identity from the recorded positions:

```bash
"$BIMSCAN_PYTHON" tools/scan_gap_visibility.py DATASET BASE_RUN density-r1-a density-r1-b \
  --output WORK/gap-visibility.json
```

This distinguishes samples where their own IFC element is visible from samples whose ray hits only a different IFC element within the 2 mm visibility tolerance. Nearly coincident or occluding surfaces can produce the latter result. It does not change the coverage score, establish a source defect, or prove that another viewpoint cannot help. Do not keep adding scans solely to resolve an identity ambiguity without investigating it.

If useful gaps remain, generate closer viewpoints around a separate planning check, then refine:

```bash
"$BIMSCAN_PYTHON" tools/scan_surface_candidates.py WORK/round1 \
  --output WORK/near-surface-candidates.json
"$BIMSCAN_PYTHON" tools/refine_density_plan.py WORK/round1 \
  --output WORK/round2 --candidates WORK/near-surface-candidates.json \
  --samples 240000 --seed 2026110901 --group-target .995 --minimum-group-support .99
```

The new plan retains the earlier positions. Capture only its appended positions, with `--start` equal to the previous plan's position count and a new run name. It also seeks 99% planning support for IFC classes and elements with at least 30 visible planning samples, so a large ground area does not hide smaller building gaps. Unreachable planning groups remain recorded. The fresh check is still a prediction; only the subsequent native capture establishes improvement. The original measurement audit remains separate.

For narrow spaces or finer features, `scan_surface_candidates.py --distances .6 1.5 3` adds a closer offset. Proposed positions still need at least 0.35 m clearance and a clear segment to an existing free-air candidate. Capture and measure any selected positions before crediting an improvement.

The predictor cache binds source, baseline, candidate set and code hashes. Use a new planning output directory after changing these inputs. Avoid repeatedly re-running full datasets to test one gap.

## Combine and hand off

Once refinement is sufficient, combine the baseline and all new capture batches once:

```bash
"$BIMSCAN_PYTHON" scan_refinement.py combine DATASET BASE_RUN density-r1-a density-r1-b \
  --output-run rtx-refined --title "Measured coverage refinement"
```

Combining retains native coordinates, remaps label indices by USD prim/IFC identity, reuses the coverage evidence and selects the combined result as the workbench's preferred run. It reports measured coverage without imposing an export acceptance threshold.

When local disk capacity permits, add `--store-remapped` to avoid recompressing remapped NPZ arrays during combination. This preserves every array value and keeps the original compressed captures. It trades larger combined files for faster writing and reading; allow space for those arrays and the combined PLY files. The default remains compressed storage. No running capture needs to be stopped to choose the combination mode.

Outputs under `DATASET/runs/RUN/` include native NPZ files, binary PLY files, `labels.json`, `capture.json`, `stations.json`, `metrics.json`, `coverage-audit.npz` and `survey-plan.html`. `DATASET/view/RUN/` contains bounded display previews. The authoritative native metadata remains in `model/building.usdc`; derived JSON caches can be rebuilt from it.

Keep runs below target while adding useful views. If geometry is enclosed, sensor response is unsupported, or measured gains have stopped, explain that limitation rather than manufacturing points or silently changing scope. Share the reusable source and selected permitted results separately from private data and internal project records.
