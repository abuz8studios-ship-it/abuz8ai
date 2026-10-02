# ABUZ8 City — Gaussian splat reconstruction
`abuz8_city.ply` — 519,593 Gaussians in standard 3D Gaussian Splatting format. Drag into SuperSplat (playcanvas.com/supersplat/editor) or Postshot.

Pipeline: concept frame → Depth Anything V2 (`depth.py`, ONNX) → per-pixel Gaussians with depth-cliff filtering and an inpainted background plate for occluded city (`splat.py`) → `.ply` + compact web format `site/img/city.az8s` (10 bytes/splat) rendered in three.js.

Next: multi-view renders of the green-Sahara city → COLMAP → trained splats.
