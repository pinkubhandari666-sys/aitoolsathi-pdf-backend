AIToolSathi Backend v1.7.0 — PDF + Pillow Image Compression

This release is based on the supplied PDF backend v1.6.0. The existing
Ghostscript /compress implementation and its Normal/High profile ladders
are retained. Pillow adds two image endpoints used by the website:
- POST /compress-jpeg
- POST /compress-image

Health:
- GET /health returns ghostscript and pillow booleans.
- GET /docs provides interactive API documentation.

Deployment:
1. Back up the current backend repository first.
2. Replace only the backend repository ROOT files with main.py,
   requirements.txt, Dockerfile and render.yaml from this package.
3. Commit to the main branch and wait for Render Auto-Deploy to finish.
4. Verify /health reports ghostscript=true and pillow=true.
5. Test /compress with a PDF and both image endpoints from /docs.

Image API form fields:
file, mode (normal|high), target_kb, max_width, max_height, quality.
For /compress-image, output_format can be original, jpeg, png or webp.
For /compress-jpeg, output is always JPEG.

Important:
- Target KB is a maximum-size goal, not a guarantee.
- PNG is lossless; quality does not reduce its color data.
- JPEG conversion of transparent images uses a white background.
- Upload limit defaults to 20 MB for images and 30 MB for PDFs.
- Do not deploy until you have backed up the current v1.6 backend.
