AIToolSathi PDF Compression Backend

Files:
- main.py: FastAPI API for PDF compression using Ghostscript
- Dockerfile: installs Ghostscript and starts Uvicorn
- requirements.txt: Python dependencies
- render.yaml: Render deployment configuration

Deploy:
1. Upload/replace these files in the GitHub repository root.
2. Render will rebuild the existing Web Service from the repository.
3. Wait until Render shows the service as Live/Deployed.
4. Test /health. The response should show ghostscript: true.
5. Test /docs -> POST /compress with a PDF.

Compression behavior (v1.2.0):
- Normal: quality-first profiles from 220 DPI / JPEG quality 92 down to 150 DPI / quality 85.
- High: quality-first profiles from 300 DPI / JPEG quality 96 down to 200 DPI / quality 90.
- The low-resolution /screen preset is no longer used.
- Selectable text and vector objects are kept as PDF objects where Ghostscript can preserve them.
- If target_kb is set, the API tries profiles from highest quality down and uses the first one that reaches the target. If none reaches it, it returns the best available quality and X-Target-Reached: false.
- If compression would make the file larger, the original PDF is returned.

Important:
The target file size is not guaranteed. Scanned PDFs with many photos may need a larger output to preserve clarity. Always inspect the downloaded PDF before using or sharing it.
