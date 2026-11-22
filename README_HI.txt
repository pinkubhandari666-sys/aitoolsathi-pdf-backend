AIToolSathi Ghostscript Backend - GitHub upload files

Upload these four files directly into the ROOT of the NEW repository:
- Dockerfile
- main.py
- requirements.txt
- render.yaml

Then in Render choose New > Blueprint and connect this repository. render.yaml and Dockerfile are both at repository root.
After deployment test: https://YOUR-RENDER-SERVICE.onrender.com/health
Expected: {"ok":true,"engine":"Ghostscript","ghostscript":true}

The API endpoint is POST /compress with multipart fields: file (PDF), mode (normal/high), target_kb (optional integer).
Target size is not guaranteed. Inspect output quality, signatures, forms and QR codes before using in production.
