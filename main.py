import asyncio
import io
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask

app = FastAPI(title="AIToolSathi PDF Compression API", version="1.7.0")
origins = [
    x.strip()
    for x in os.getenv(
        "ALLOWED_ORIGINS",
        "https://aitoolsathi.in,https://www.aitoolsathi.in",
    ).split(",")
    if x.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

MAX_BYTES = int(os.getenv("MAX_UPLOAD_MB", "30")) * 1024 * 1024
# Keep the small Render free instance from running multiple Ghostscript jobs at once.
COMPRESSION_SEMAPHORE = asyncio.Semaphore(1)
GS = os.getenv("GHOSTSCRIPT_BIN", "gs")

# Profiles are ordered from best visual quality to stronger compression.
# Avoid very low-resolution presets by default; the target-size ladder is
# allowed to descend only to 90 DPI.
PROFILES = {
    # Many scanned PDFs are already only 85-175 DPI. Profiles above their
    # source resolution do not downsample them, so they barely reduce size.
    # These ladders step down gradually while keeping text reasonably legible.
    "normal": [(140, 92), (120, 90), (110, 88), (100, 85), (90, 82)],
    # High mode now tries finer, higher-resolution settings first. This gives
    # PDFs that previously landed well below the target (for example ~692 KB
    # against an 800 KB target) a chance to use more pixels while still meeting
    # the requested limit. The existing safer ladder remains as fallback.
    "high": [
        (220, 98), (210, 98), (200, 97), (190, 97),
        (180, 96), (170, 96), (160, 95), (150, 94),
        (140, 93), (120, 90), (110, 88), (100, 85), (90, 82)
    ],
}


IMAGE_MAX_BYTES = int(os.getenv("MAX_IMAGE_UPLOAD_MB", "20")) * 1024 * 1024
IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}
IMAGE_PROFILES = {
    "normal": [92, 88, 84, 80, 76, 72, 68, 64, 60],
    "high": [88, 82, 76, 70, 64, 58, 52, 46, 40],
}

def _image_bytes(image, fmt, quality):
    out = io.BytesIO()
    save_kwargs = {}
    if fmt in ("JPEG", "WEBP"):
        save_kwargs["quality"] = int(quality)
        save_kwargs["optimize"] = True
        if fmt == "JPEG":
            save_kwargs["progressive"] = True
    elif fmt == "PNG":
        save_kwargs["optimize"] = True
        # PNG is lossless; quality does not control its compression ratio.
    image.save(out, format=fmt, **save_kwargs)
    return out.getvalue()

def _prepare_image(raw, output_format, max_width, max_height):
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except Exception as exc:
        raise HTTPException(400, "Invalid or unsupported image file.") from exc
    if image.format not in IMAGE_FORMATS:
        raise HTTPException(400, "Only JPG, JPEG, PNG and WebP images are supported.")
    original_format = image.format
    image = ImageOps.exif_transpose(image)
    fmt = (output_format or "original").strip().lower()
    if fmt == "original":
        fmt = original_format.lower()
    if fmt in ("jpg", "jpeg"):
        fmt = "jpeg"
    if fmt not in ("jpeg", "png", "webp"):
        raise HTTPException(400, "Output format must be original, jpeg, png or webp.")
    pil_fmt = {"jpeg": "JPEG", "png": "PNG", "webp": "WEBP"}[fmt]

    if max_width > 0 or max_height > 0:
        w, h = image.size
        if max_width > 0 and max_height > 0:
            # Fit inside the requested box while preserving aspect ratio.
            scale = min(max_width / w, max_height / h, 1.0)
        elif max_width > 0:
            scale = min(max_width / w, 1.0)
        else:
            scale = min(max_height / h, 1.0)
        if scale < 1:
            image = image.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.LANCZOS)

    if pil_fmt == "JPEG":
        if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
            rgba = image.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.getchannel("A"))
            image = background
        else:
            image = image.convert("RGB")
    elif pil_fmt == "PNG" and image.mode not in ("RGB", "RGBA", "L", "LA", "P"):
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    elif pil_fmt == "WEBP" and image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    return image, pil_fmt, fmt

async def _compress_image_response(
    file: UploadFile, mode: str, target_kb: int, max_width: int,
    max_height: int, quality: int, output_format: str, force_jpeg: bool
):
    if not file.filename:
        raise HTTPException(400, "Please upload an image file.")
    raw = await file.read(IMAGE_MAX_BYTES + 1)
    if not raw or len(raw) > IMAGE_MAX_BYTES:
        raise HTTPException(
            413 if len(raw) > IMAGE_MAX_BYTES else 400,
            f"Image must be non-empty and under {IMAGE_MAX_BYTES // (1024 * 1024)} MB."
        )
    if mode not in IMAGE_PROFILES:
        raise HTTPException(400, "Mode must be normal or high.")
    image, pil_fmt, fmt_name = _prepare_image(
        raw, "jpeg" if force_jpeg else output_format, max_width, max_height
    )
    if quality < 0 or quality > 95:
        raise HTTPException(400, "Quality must be between 0 and 95.")
    target_bytes = target_kb * 1024 if target_kb > 0 else 0
    qualities = [quality] if quality > 0 else IMAGE_PROFILES[mode]
    candidates = []
    for q in qualities:
        data = _image_bytes(image, pil_fmt, q)
        candidates.append((data, q))
        if target_bytes and len(data) <= target_bytes:
            break
    # For a target-size request, preserve the highest-quality candidate that
    # reaches the target. If none reaches it, return the smallest tested result.
    if target_bytes:
        fitting = [item for item in candidates if len(item[0]) <= target_bytes]
        result, used_q = fitting[0] if fitting else candidates[-1]
        reached = len(result) <= target_bytes
    else:
        result, used_q = candidates[0]
        reached = True

    # Never return a larger file when the source can be preserved unchanged.
    if len(result) >= len(raw) and not (max_width or max_height) and not force_jpeg:
        result = raw
        fmt_name = (Image.open(io.BytesIO(raw)).format or pil_fmt).lower()
        reached = (not target_bytes) or len(result) <= target_bytes
    media = {"jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(fmt_name, "application/octet-stream")
    ext = "jpg" if fmt_name == "jpeg" else fmt_name
    headers = {
        "X-Original-Bytes": str(len(raw)),
        "X-Compressed-Bytes": str(len(result)),
        "X-Target-Reached": "true" if reached else "false",
        "X-Output-Format": media,
        "X-Image-Quality": str(used_q),
    }
    return Response(
        content=result, media_type=media, headers=headers
    )

@app.post("/compress-jpeg")
async def compress_jpeg(
    file: UploadFile = File(...),
    mode: str = Form("normal"),
    target_kb: int = Form(0),
    max_width: int = Form(0),
    max_height: int = Form(0),
    quality: int = Form(0),
):
    return await _compress_image_response(
        file, mode, target_kb, max_width, max_height, quality, "jpeg", True
    )

@app.post("/compress-image")
async def compress_image(
    file: UploadFile = File(...),
    mode: str = Form("normal"),
    target_kb: int = Form(0),
    max_width: int = Form(0),
    max_height: int = Form(0),
    quality: int = Form(0),
    output_format: str = Form("original"),
):
    return await _compress_image_response(
        file, mode, target_kb, max_width, max_height, quality, output_format, False
    )



@app.get("/")
def root():
    return {
        "service": "AIToolSathi PDF Compression API",
        "status": "ok",
        "engine": "Ghostscript + Pillow",
        "version": "1.7.0",
        "note": "Quality-first compression; selectable text and vector content are retained where possible.",
    }


@app.get("/health")
def health():
    return {
        "ok": True,
        "engine": "Ghostscript + Pillow",
        "version": "1.7.0",
        "ghostscript": shutil.which(GS) is not None,
        "pillow": True,
    }


@app.post("/compress")
async def compress_pdf(
    file: UploadFile = File(...),
    mode: str = Form("normal"),
    target_kb: int = Form(0),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file.")
    if mode not in PROFILES:
        raise HTTPException(400, "Mode must be normal or high.")

    raw = await file.read(MAX_BYTES + 1)
    if not raw or len(raw) > MAX_BYTES:
        raise HTTPException(
            413 if len(raw) > MAX_BYTES else 400,
            f"PDF must be non-empty and under {MAX_BYTES // (1024 * 1024)} MB.",
        )
    if not raw.startswith(b"%PDF-"):
        raise HTTPException(400, "Invalid PDF file.")
    if not shutil.which(GS):
        raise HTTPException(503, "Ghostscript is not installed on this service.")

    work = Path(tempfile.mkdtemp(prefix="ats-pdf-"))
    src = work / "input.pdf"
    src.write_bytes(raw)
    outputs = []
    target_bytes = target_kb * 1024 if target_kb > 0 else 0

    try:
        async with COMPRESSION_SEMAPHORE:
            for idx, (dpi, jpeg_quality) in enumerate(PROFILES[mode]):
                out = work / f"output-{idx}.pdf"
                cmd = [
                    GS,
                    "-sDEVICE=pdfwrite",
                    "-dCompatibilityLevel=1.5",
                    "-dNOPAUSE",
                    "-dBATCH",
                    "-dSAFER",
                    "-dQUIET",
                    "-dDetectDuplicateImages=true",
                    "-dCompressFonts=true",
                    "-dSubsetFonts=true",
                    "-dAutoRotatePages=/None",
                    # /default avoids the aggressive /screen and /ebook presets.
                    "-dPDFSETTINGS=/default",
                    "-dDownsampleColorImages=true",
                    "-dColorImageDownsampleType=/Bicubic",
                    f"-dColorImageResolution={dpi}",
                    "-dColorImageFilter=/DCTEncode",
                    "-dAutoFilterColorImages=false",
                    "-dDownsampleGrayImages=true",
                    "-dGrayImageDownsampleType=/Bicubic",
                    f"-dGrayImageResolution={dpi}",
                    "-dGrayImageFilter=/DCTEncode",
                    "-dAutoFilterGrayImages=false",
                    f"-dJPEGQ={jpeg_quality}",
                    "-dDownsampleMonoImages=true",
                    "-dMonoImageDownsampleType=/Subsample",
                    "-dMonoImageResolution=300",
                    f"-sOutputFile={out}",
                    str(src),
                ]
                try:
                    run = await asyncio.to_thread(
                        subprocess.run, cmd, capture_output=True, text=True, timeout=120
                    )
                except subprocess.TimeoutExpired:
                    continue

                if run.returncode == 0 and out.exists() and out.stat().st_size > 0:
                    outputs.append({"path": out, "dpi": dpi, "quality": jpeg_quality})
                    # Profiles are quality-ordered. Stop at the first one that meets
                    # the requested size, preserving the best quality that can meet it.
                    if target_bytes and out.stat().st_size <= target_bytes:
                        break
                    # Without a size target, the first successful profile is preferred.
                    if not target_bytes:
                        break
        if not outputs:
            raise HTTPException(422, "Could not optimize this PDF. Original file was not changed.")

        # Profiles are quality-ordered. If no profile reaches the target,
        # preserve the first successful (highest-quality) result instead of
        # choosing the smallest, potentially over-compressed output.
        if target_bytes and outputs[-1]["path"].stat().st_size > target_bytes:
            candidate_info = outputs[0]
        else:
            candidate_info = outputs[-1]
        candidate = candidate_info["path"]
        if candidate.stat().st_size >= len(raw):
            best = work / "original-preserved.pdf"
            best.write_bytes(raw)
            used_dpi = 0
            used_quality = 0
        else:
            best = candidate
            used_dpi = candidate_info["dpi"]
            used_quality = candidate_info["quality"]

        size = best.stat().st_size
        reached = (not target_bytes) or size <= target_bytes
        return FileResponse(
            best,
            media_type="application/pdf",
            filename="compressed.pdf",
            headers={
                "X-Original-Bytes": str(len(raw)),
                "X-Compressed-Bytes": str(size),
                "X-Target-Reached": "true" if reached else "false",
                "X-Image-DPI": str(used_dpi),
                "X-JPEG-Quality": str(used_quality),
            },
            background=BackgroundTask(shutil.rmtree, work, ignore_errors=True),
        )
    except HTTPException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(work, ignore_errors=True)
        raise HTTPException(500, "PDF compression failed. Please try another PDF.") from exc
