AIToolSathi PDF Compression Backend v1.4.0

Files:
- main.py: FastAPI PDF compression API using Ghostscript
- Dockerfile: installs Ghostscript and starts Uvicorn
- requirements.txt: Python dependencies
- render.yaml: Render deployment configuration

Deploy:
1. Replace the files in the ROOT of the existing GitHub backend repository.
2. Commit the changes. Render Auto-Deploy should rebuild the existing service.
3. Wait until Render shows Live/Deployed.
4. Test /health; ghostscript must be true.
5. Test /docs -> POST /compress with a PDF.

Compression:
- Normal and High use a gradual image-resolution/JPEG-quality ladder.
- High v1.4 tries finer 170-220 DPI / higher JPEG-quality profiles first, so it can use more of an 800 KB target when possible.
- The ladder is designed for scanned PDFs whose embedded images may already be around 85-175 DPI.
- If target_kb is met, the first (highest-quality) profile that reaches it is returned.
- If target_kb cannot be met without going below the quality floor, the smallest tested output is returned and X-Target-Reached is false.
- If compression would make the file larger, the original PDF is returned.
- The API does not guarantee a 500 KB output. For photo/scanned PDFs, 500 KB may require visibly lower resolution. Check legibility before using official documents.

Response headers:
- X-Original-Bytes
- X-Compressed-Bytes
- X-Target-Reached
- X-Image-DPI
- X-JPEG-Quality

Note:
Ghostscript may rewrite PDF internals and remove existing PDF encryption/permissions. Do not use this backend on password-protected or restricted files unless you have permission and have confirmed the output is acceptable.
