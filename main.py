import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

app = FastAPI(title="AIToolSathi PDF Compression API", version="1.2.0")
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
GS = os.getenv("GHOSTSCRIPT_BIN", "gs")

# Profiles are ordered from best visual quality to stronger compression.
# We intentionally avoid Ghostscript's /screen preset (typically 72-96 DPI),
# which can make scanned certificates and small text look soft.
PROFILES = {
    "normal": [(220, 92), (200, 90), (180, 88), (150, 85)],
    "high": [(300, 96), (250, 94), (220, 92), (200, 90)],
}


@app.get("/")
def root():
    return {
        "service": "AIToolSathi PDF Compression API",
        "status": "ok",
        "engine": "Ghostscript",
        "version": "1.2.0",
        "note": "Quality-first compression; selectable text and vector content are retained where possible.",
    }


@app.get("/health")
def health():
    return {"ok": True, "engine": "Ghostscript", "ghostscript": shutil.which(GS) is not None}


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
                run = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
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

        # If no profile met the requested size, keep the largest successful
        # output: it normally retains the most image detail. Report target_kb
        # honestly instead of silently choosing a very low-resolution result.
        if target_bytes and outputs[-1]["path"].stat().st_size > target_bytes:
            candidate_info = max(outputs, key=lambda item: item["path"].stat().st_size)
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
