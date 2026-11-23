AIToolSathi PDF Compression Backend v1.6.0

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

Changes in v1.6.0:
- Ghostscript subprocess runs in a worker thread so the FastAPI event loop can continue answering /health.
- A semaphore limits the service to one Ghostscript compression job at a time to protect the small Render free instance.
- If no tested profile can meet the requested target, the API returns the highest-quality successful result and reports X-Target-Reached=false, rather than selecting the smallest/lowest-quality result.
- Existing Normal/High profile settings and API form fields are preserved.

Important:
- Target KB is a maximum size goal, not a guarantee of an exact output size.
- If the target cannot be reached without reducing quality too much, the response may be larger than the target. Check document legibility.
- If compression would make the file larger, the original PDF is returned.

Response headers:
- X-Original-Bytes
- X-Compressed-Bytes
- X-Target-Reached
- X-Image-DPI
- X-JPEG-Quality

Note:
Ghostscript may rewrite PDF internals and remove existing PDF encryption/permissions. Do not use this backend on password-protected or restricted files unless you have permission and have confirmed the output is acceptable.
