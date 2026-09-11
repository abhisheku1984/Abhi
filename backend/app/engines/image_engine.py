"""Image engine: generation, editing, restoration and transformation (spec §11).

Every operation either performs real pixel work or fails with a precise,
machine-readable reason. Operations that need a neural network
(background removal, face restoration, pose/depth control, semantic object
removal) route through their adapters and report MODEL NOT AVAILABLE /
HARDWARE REQUIREMENT NOT MET when the weights or GPU are missing.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.adapters.registry import registry
from app.core.errors import ErrorCode, StudioError
from app.db.models import Asset, Generation, Job, User
from app.engines.base import build_request, mark_generation_failed, persist_result, workdir
from app.services import assets as asset_svc
from app.services import media
from app.services import safety

# Operations that require a neural model (no CPU fallback exists).
MODEL_ONLY_OPS = {
    "background-removal": ("background_removal", "Background removal needs a segmentation model (u2net/rembg)."),
    "face-restoration": ("face_restoration", "Face restoration needs CodeFormer or GFPGAN weights."),
    "pose-to-image": ("pose_to_image", "Pose control needs ControlNet + a pose detector."),
    "depth-to-image": ("depth_to_image", "Depth control needs ControlNet + a depth estimator."),
}

# Operations implemented with real Pillow processing (always available).
CPU_EDIT_OPS = {"upscale", "resize", "crop", "rotate", "flip", "filter", "adjust",
                "text", "watermark", "vignette", "grayscale", "blur", "sharpen", "remove-background-alpha"}


def available_operations() -> list[dict]:
    """Used by /api/models/operations so the UI reflects reality."""
    rows = []
    for op, (capability, note) in MODEL_ONLY_OPS.items():
        status = "model_required"
        adapters = registry.by_capability(capability)
        if adapters and any(a.status().available for a in adapters):
            status = "available"
        rows.append({"op": op, "capability": capability, "status": status, "note": note,
                     "adapters": [a.key for a in adapters]})
    for op in sorted(CPU_EDIT_OPS):
        rows.append({"op": op, "capability": "image_edit", "status": "available",
                     "note": "Real CPU image processing (Pillow).", "adapters": ["builtin"]})
    for op, mode in (("text-to-image", "text_to_image"), ("image-to-image", "image_to_image"),
                     ("inpainting", "inpainting"), ("outpainting", "outpainting"),
                     ("background-generation", "background_replace"),
                     ("object-removal", "inpainting"), ("object-replacement", "inpainting"),
                     ("sketch-to-image", "sketch_to_image"), ("reference-to-image", "reference_to_image"),
                     ("multi-reference-image", "multi_reference_image"), ("style-transfer", "style_transfer")):
        adapters = registry.by_capability(mode)
        rows.append({"op": op, "capability": mode,
                     "status": "available" if any(a.status().available for a in adapters) else "model_required",
                     "note": "", "adapters": [a.key for a in adapters]})
    return rows


def _edit_image(src_path: str, out_path: str, op: str, params: dict) -> str:
    """Deterministic, real image processing."""
    im = media.load_image(src_path)
    if op == "upscale":
        im = media.upscale_image(im, float(params.get("scale", 2.0)), params.get("method", "lanczos"))
    elif op == "resize":
        im = media.resize_image(im, int(params.get("width", im.width)), int(params.get("height", im.height)),
                                params.get("method", "lanczos"), bool(params.get("keep_aspect", False)))
    elif op == "crop":
        box = params.get("box")
        im = media.crop_image(im, tuple(box) if box else None, params.get("aspect"))
    elif op == "rotate":
        im = im.rotate(float(params.get("angle", 0)), expand=True)
    elif op == "flip":
        from PIL import ImageOps

        im = ImageOps.mirror(im) if params.get("direction") == "horizontal" else ImageOps.flip(im)
    elif op == "filter":
        im = media.apply_filter(im, str(params.get("preset", "cinematic")), float(params.get("strength", 1.0)))
    elif op == "adjust":
        im = media.adjust_image(im, float(params.get("brightness", 1.0)), float(params.get("contrast", 1.0)),
                                float(params.get("saturation", 1.0)), float(params.get("sharpness", 1.0)),
                                float(params.get("blur", 0.0)))
    elif op == "text":
        im = media.add_text(im, str(params.get("text", "")), str(params.get("position", "bottom")),
                            int(params.get("font_size", 48)), str(params.get("color", "white")),
                            str(params.get("stroke", "black")))
    elif op == "watermark":
        im = media.watermark(im, str(params.get("text", "")), float(params.get("opacity", 0.35)),
                             str(params.get("position", "bottom-right")))
    elif op == "vignette":
        im = media.add_vignette(im, float(params.get("strength", 0.5)))
    elif op == "grayscale":
        im = im.convert("L").convert("RGB")
    elif op == "blur":
        im = media.adjust_image(im, blur=float(params.get("radius", 2.0)))
    elif op == "sharpen":
        im = media.adjust_image(im, sharpness=float(params.get("amount", 2.0)))
    elif op == "remove-background-alpha":
        # Flatten onto a colour (or transparency) - explicit, not "AI removal".
        bg = params.get("color", "#ffffff")
        if params.get("transparent"):
            im = im.convert("RGBA")
        else:
            from PIL import Image

            base = Image.new("RGB", im.size, bg)
            base.paste(im, mask=im.split()[-1] if im.mode == "RGBA" else None)
            im = base
    else:
        raise StudioError(f"Unsupported image operation '{op}'.", code=ErrorCode.INVALID_REQUEST)
    return media.save_image(im, out_path)


def run_image_job(db: Session, job: Job, progress, cancel_event) -> dict:
    params = dict(job.params or {})
    user = db.get(User, job.owner_id)
    if user is None:
        raise StudioError("Job owner not found.", code=ErrorCode.NOT_FOUND)
    generation = db.get(Generation, job.generation_id) if job.generation_id else None
    op = str(params.get("operation") or "text-to-image").lower()
    out_dir = workdir("image")
    progress(0.02, f"preparing {op}")

    try:
        # ---- CPU image editing --------------------------------------------
        if op in CPU_EDIT_OPS:
            src_id = params.get("asset_id") or (params.get("references") or [{}])[0].get("asset_id")
            if not src_id:
                raise StudioError("An input asset is required for this operation.", code=ErrorCode.INVALID_REQUEST)
            asset = db.get(Asset, src_id)
            if not asset or asset.owner_id != user.id:
                raise StudioError("Source asset not found.", code=ErrorCode.NOT_FOUND, status_code=404)
            src = asset_svc.asset_abs_path(asset)
            out = str(out_dir / f"{op}_{Path(src).stem}.png")
            progress(0.3, f"applying {op}")
            _edit_image(src, out, op, params)
            progress(0.8, "saving result")
            new_assets = persist_result(
                db, user=user,
                result=_wrap(out, "image", op),
                generation=generation,
                adapter=_builtin_adapter(),
                project_id=job.project_id,
                parent_asset_id=asset.id,
                asset_meta={"operation": op},
            )
            return _done(new_assets, op, "builtin")

        # ---- model-only operations ----------------------------------------
        if op in MODEL_ONLY_OPS:
            capability, note = MODEL_ONLY_OPS[op]
            adapter = registry.resolve(capability, params.get("model_id"))
            req = build_request(db, user, params, str(out_dir), capability)
            req.cancel_event = cancel_event
            progress(0.15, f"running {adapter.name}")
            result = adapter.generate(req, progress)
            new_assets = persist_result(db, user=user, result=result, generation=generation,
                                        adapter=adapter, project_id=job.project_id,
                                        parent_asset_id=params.get("asset_id"))
            return _done(new_assets, op, adapter.key)

        # ---- generative operations ----------------------------------------
        mode = _op_to_mode(op)
        adapter = registry.resolve(mode, params.get("model_id"))
        safety.enforce(params.get("prompt", ""), allow_impersonation=bool(params.get("identity_rights")))
        req = build_request(db, user, {**params, "mode": mode}, str(out_dir), mode)
        req.cancel_event = cancel_event
        req.validate = None  # type: ignore[assignment]
        adapter.validate(req)
        est = adapter.estimate(req)
        progress(0.1, f"generating with {adapter.name}")
        result = adapter.generate(req, progress)
        if not result.files:
            raise StudioError(f"{adapter.name} produced no output.", code=ErrorCode.INTERNAL_ERROR)
        new_assets = persist_result(db, user=user, result=result, generation=generation,
                                    adapter=adapter, project_id=job.project_id,
                                    parent_asset_id=params.get("asset_id"))
        return _done(new_assets, op, adapter.key, {"estimate": est.__dict__})
    except Exception as exc:  # noqa: BLE001
        mark_generation_failed(db, generation, getattr(exc, "code", ErrorCode.INTERNAL_ERROR), str(exc))
        raise
    finally:
        progress(0.99, "finalising")


def _op_to_mode(op: str) -> str:
    mapping = {
        "text-to-image": "text_to_image",
        "image-to-image": "image_to_image",
        "inpainting": "inpainting",
        "outpainting": "outpainting",
        "background-generation": "background_replace",
        "background-replacement": "background_replace",
        "object-removal": "inpainting",
        "object-replacement": "inpainting",
        "sketch-to-image": "sketch_to_image",
        "reference-to-image": "reference_to_image",
        "multi-reference-image": "multi_reference_image",
        "style-transfer": "style_transfer",
    }
    if op not in mapping:
        raise StudioError(
            f"Unsupported image operation '{op}'.",
            code=ErrorCode.INVALID_REQUEST,
            details={"supported": sorted(mapping) + sorted(CPU_EDIT_OPS) + sorted(MODEL_ONLY_OPS)},
        )
    return mapping[op]


def _wrap(path: str, kind: str, op: str):
    from app.adapters.base import GeneratedFile, GenerationResult

    return GenerationResult(files=[GeneratedFile(path, kind, meta={"operation": op, "renderer": "pillow"})],
                            meta={"operation": op, "renderer": "pillow"})


def _builtin_adapter():
    from app.adapters.base import ImageModelAdapter

    class _Builtin(ImageModelAdapter):
        key = "builtin_image_ops"
        name = "Built-in image processing"

        def generate(self, req, progress):  # pragma: no cover - not used directly
            raise StudioError("Use run_image_job for built-in operations.", code=ErrorCode.INVALID_REQUEST)

    return _Builtin()


def _done(assets: list[Asset], op: str, adapter_key: str, extra: dict | None = None) -> dict:
    return {
        "asset_ids": [a.id for a in assets],
        "assets": [
            {
                "id": a.id, "name": a.name, "kind": a.kind, "url": f"/api/assets/{a.id}/content",
                "thumbnail_url": f"/api/assets/{a.id}/thumbnail",
                "width": a.width, "height": a.height, "mime_type": a.mime_type,
                "size_bytes": a.size_bytes,
            }
            for a in assets
        ],
        "operation": op,
        "adapter": adapter_key,
        **(extra or {}),
    }
