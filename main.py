import os, shutil, subprocess, tempfile
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

app = FastAPI(title="AIToolSathi PDF Compression API", version="1.1.0")
origins = [x.strip() for x in os.getenv("ALLOWED_ORIGINS", "https://aitoolsathi.in,https://www.aitoolsathi.in").split(",") if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False, allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["*"])
MAX_BYTES = int(os.getenv("MAX_UPLOAD_MB", "30")) * 1024 * 1024
GS = os.getenv("GHOSTSCRIPT_BIN", "gs")

@app.get("/")
def root():
    return {"service": "AIToolSathi PDF Compression API", "status": "ok", "engine": "Ghostscript", "note": "Backend ready; frontend connection requires deployed API URL."}

@app.get("/health")
def health():
    return {"ok": True, "engine": "Ghostscript", "ghostscript": shutil.which(GS) is not None}

@app.post("/compress")
async def compress_pdf(file: UploadFile = File(...), mode: str = Form("normal"), target_kb: int = Form(0)):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file.")
    if mode not in ("normal", "high"):
        raise HTTPException(400, "Mode must be normal or high.")
    raw = await file.read(MAX_BYTES + 1)
    if not raw or len(raw) > MAX_BYTES:
        raise HTTPException(413 if len(raw) > MAX_BYTES else 400, f"PDF must be non-empty and under {MAX_BYTES // (1024*1024)} MB.")
    if not raw.startswith(b"%PDF-"):
        raise HTTPException(400, "Invalid PDF file.")
    if not shutil.which(GS):
        raise HTTPException(503, "Ghostscript is not installed on this service.")

    work = Path(tempfile.mkdtemp(prefix="ats-pdf-"))
    src = work / "input.pdf"
    src.write_bytes(raw)
    # pdfwrite rewrites PDF objects instead of rasterizing every page. Selectable text/vector
    # are generally retained; scanned-image pages remain images and may be downsampled.
    if target_kb > 0:
        profiles = ["/screen", "/ebook"] if mode == "normal" else ["/ebook", "/printer"]
    else:
        profiles = ["/printer", "/ebook"] if mode == "normal" else ["/prepress", "/printer"]
    outputs = []
    try:
        for idx, profile in enumerate(profiles):
            out = work / f"output-{idx}.pdf"
            resolution = "96" if profile == "/screen" else ("150" if profile == "/ebook" else ("220" if mode == "high" else "200"))
            cmd = [GS, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.5", "-dNOPAUSE", "-dBATCH", "-dSAFER", "-dQUIET",
                   "-dDetectDuplicateImages=true", "-dCompressFonts=true", "-dSubsetFonts=true", "-dAutoRotatePages=/None",
                   f"-dPDFSETTINGS={profile}", "-dDownsampleColorImages=true", "-dColorImageDownsampleType=/Bicubic",
                   "-dDownsampleGrayImages=true", "-dGrayImageDownsampleType=/Bicubic", "-dDownsampleMonoImages=true",
                   "-dMonoImageDownsampleType=/Subsample", f"-dColorImageResolution={resolution}",
                   f"-dGrayImageResolution={resolution}", "-dMonoImageResolution=300", f"-sOutputFile={out}", str(src)]
            try:
                run = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            except subprocess.TimeoutExpired:
                continue
            if run.returncode == 0 and out.exists() and out.stat().st_size > 0:
                outputs.append(out)
        if not outputs:
            shutil.rmtree(work, ignore_errors=True)
            raise HTTPException(422, "Could not optimize this PDF. Original file was not changed.")
        # High mode prefers the largest valid Ghostscript output (quality first);
        # normal mode prefers the smallest. Never inflate an already-optimized PDF:
        # if Ghostscript output is larger than the upload, return the original bytes.
        candidate = (max(outputs, key=lambda p: p.stat().st_size) if mode == "high" else min(outputs, key=lambda p: p.stat().st_size))
        if candidate.stat().st_size >= len(raw):
            best = work / "original-preserved.pdf"
            best.write_bytes(raw)
        else:
            best = candidate
        size = best.stat().st_size
        reached = (not target_kb) or size <= target_kb * 1024
        return FileResponse(best, media_type="application/pdf", filename="compressed.pdf",
            headers={"X-Original-Bytes": str(len(raw)), "X-Compressed-Bytes": str(size),
                     "X-Target-Reached": "true" if reached else "false"},
            background=BackgroundTask(shutil.rmtree, work, ignore_errors=True))
    except HTTPException:
        raise
    except Exception as exc:
        shutil.rmtree(work, ignore_errors=True)
        raise HTTPException(500, "PDF compression failed. Please try another PDF.") from exc
