---
name: rtx-scan-planning
description: Plan and improve native RTX LiDAR coverage of IFC-derived USD buildings using Isaac Sim. Use for scanner placement, coverage-gap investigation, incremental captures and IFC-linked point-cloud output.
metadata:
  prototype_version: "0.7.0.3"
---

# RTX scan planning

Use the supplied building and its native IFC identities to produce measured scan coverage. The useful output is a point cloud, its IFC associations, and an explanation of remaining gaps. The workflow adds sensor viewpoints; it does not repair or replace building geometry.

## Runtime

For a new machine, follow [setup and prerequisites](README.md), then run `scripts/preflight.py` with the requested GPU, Isaac Sim and optional CAD checks. This skill targets supported Isaac Sim installations on Windows and Linux, including x86-64 and DGX Spark; it has no ARM-only requirement. Configure the installed runtime paths instead of assuming a host layout. The retained full-capture evidence used Isaac Sim 5.1 on DGX Spark; a different platform or Isaac version needs a small native capture before a large job.

Read [the command reference](references/workflow.md) before execution. Use an existing BIM Scan Workbench installation when supplied, or the included `runtime/`. Planning, Isaac Sim and CAD conversion can use separate Python environments. Run the Windows commands through PowerShell with `& $env:BIMSCAN_PYTHON`; the reference uses Bash syntax. Vendor runtimes are separate dependencies.

IFC ingestion uses the standard NVIDIA CAD Converter. Prepared workbench datasets can be reused directly. Native USD remains the metadata authority. Point-cloud label indices are a transport mapping to USD prim paths and IFC GlobalIds, not an alternative IFC database.

## Plan and execute

1. Establish the input revision, sensor profile and requested coverage criterion. Use an existing completed scan as the baseline when available. Preserve its audit samples and profile for a comparable refinement. The supplied example criterion is 20 mm support over 99% of visible audit samples; it is an experiment setting, not a professional surveying standard.
2. Include all physical geometry in the requested asset: interiors, stairs, exterior walls, roofs, terrain, paving and context. Identify where positions are missing and where existing views produce sparse points. Do not remove large surfaces from the denominator to improve a score. Report buried, enclosed and otherwise unobserved surfaces separately.
3. Generate candidate positions and check clearance, range, field of view and occlusion. For synthetic scans, elevated or otherwise difficult-to-access positions are valid when the task allows them. A field-survey plan additionally needs realistic scanner access and setup constraints.
4. Use the recorded beam pattern to estimate point spacing, then choose additional positions. Line of sight alone does not establish dense coverage. Keep planning samples separate from the retained measurement samples. Fresh planning checks may become inputs to later planning rounds; the final measurement audit must stay unchanged.
5. Capture the new positions with native Isaac Sim RTX LiDAR, then measure the resulting points. Reuse completed captures. Assign distinct position IDs across batches and rounds. Parallel batches must write separate run directories; combine and publish them sequentially.
6. Compare the actual union of old and new returns. Inspect overall support, surface groups, IFC elements and newly visible area. A large terrain improvement must not conceal remaining building gaps. Use the largest actionable gaps to choose the next views, then capture and measure again. For stubborn gaps, check first-hit identity: nearly coincident IFC surfaces can pass a distance-based visibility test while a different element owns the return. Keep that limitation separate from sparse coverage; the diagnostic must not silently change the score.

When authorised to improve coverage, continue through this loop after finding an actionable gap. Do not stop at a diagnosis or a queued job. Stop when the agreed criteria and priority areas are covered, no useful additional views can be found, a concrete execution blocker prevents progress, or the user stops the work. There is no built-in maximum scan count. Explain any residual gap that cannot be resolved within the supplied geometry and sensor model.

For a diagnosis-only request, inspect existing evidence without starting capture jobs unless the session already authorises refinement.

## Measurement and output

- **Coverage:** sampled surface locations with a recorded return within the chosen distance and with the same IFC identity. Report the visible denominator and all-surface support distinctly, including how the visible area changed.
- **Geometric agreement:** distance from recorded points to the supplied USD mesh. This is separate from coverage and does not establish physical scanner calibration or IFC conversion fidelity.
- **Identity:** native RTX stable object ID → USD prim → IFC GlobalId and existing USD properties. Check unresolved labels explicitly.

Combine only compatible source revisions and sensor configurations. Preserve coordinates and provenance; never fill holes with sampled mesh points, move returns to fit geometry, or report a prediction as a capture. Measured outputs remain useful even when coverage is below target; retain them while improving the plan.

Deliver the completed point-cloud files, label table, coverage and error measurements, position plan, and a concise before/after result. Keep the original data and audit available. State that a bounded viewer preview is downsampled; display-point size changes are not changes to scan accuracy.

## Current prototype limits

The density predictor supports the tested stationary, unrotated, uniform angular grid with zero configured range/angular noise. It rejects noisy or rotated profiles. Other beam patterns require a corresponding predictor or a measured candidate-capture approach. Do not silently substitute an automotive scanner or claim manufacturer-equivalent behaviour.

The tested geometry baseline treats surfaces as opaque. Material appearance is not a validated LiDAR response model; glass, transmission, reflectance and manufacturer calibration require separate tests. Use the capture runtime actually available on the machine rather than assuming another Isaac Sim version is compatible.

The package contains source and skill guidance. It does not include datasets, vendor SDKs, credentials, internal project records or permission to publish customer assets.
