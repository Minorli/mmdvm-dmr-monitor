# Third-party notices

Starwave application code is distributed under the MIT license. Dependencies
and bundled assets retain their own licenses; MIT does not replace them.

- The software DMR adapter and mbelib retain ISC notices in
  [native/THIRD_PARTY_LICENSES.txt](native/THIRD_PARTY_LICENSES.txt).
  Pinned sources and upstream's separate vocoder patent notice are described in
  [native/README.md](native/README.md).
- The bundled Noto regional flag font retains its SIL Open Font License under
  `web/public/fonts/`; both font and notice are included in the web build.
- Docker builds a pinned upstream SQLite distribution. Its public-domain
  declaration and verified source hashes are included in the image's SQLite
  provenance file; see [deploy/build_sqlite.py](deploy/build_sqlite.py).
- React, Arco Design, Vite, faster-whisper, CTranslate2, PyAV, HTTPX, FastAPI,
  Uvicorn and other installed dependencies retain the license metadata shipped
  in their respective packages. Python wheel license files remain in the image.
- Speech model weights are downloaded at runtime into the model volume. They
  are not included in the repository or Docker build context. Check the selected
  model's license when distributing a prefilled model volume.

This repository contains protocol fixtures, not a recording corpus. Live audio,
transcripts, SSH credentials and API credentials are excluded from source and
image builds.
