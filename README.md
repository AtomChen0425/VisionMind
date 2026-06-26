# PhotoManager

Desktop application for AI-assisted photo library analysis and Lightroom-compatible tag injection on Windows and macOS.

## Goals
- Incremental library scanning
- Automatic background monitoring while the app is open
- GPU-first AI keyword generation with `open-clip-torch`
- ExifTool-based metadata tag writing in place via `pyexiftool`
- Local SQLite indexing
- FAISS-backed vector similarity search
- No background service, no web server, no Docker
- Launch on demand and exit completely when the app closes

## Current Architecture
- `src/core/database.py`: SQLite schema, dedup-safe writes, scan run tracking, tag and embedding storage
- `src/core/scanner.py`: incremental filesystem scan and change detection
- `src/core/analyzer.py`: optional open-clip wrapper for tag and embedding generation
- `src/core/exiftool_metadata.py`: ExifTool-based metadata writer via `pyexiftool`
- `src/core/exiftool_manager.py`: local ExifTool discovery and download helper
- `src/core/vector_index.py`: FAISS index management
- `src/core/semantic_search.py`: mixed filename + semantic search coordinator
- `src/core/pipeline.py`: end-to-end file processing pipeline
- `src/gui`: PySide6 desktop UI and worker threads

## Run
```bash
python -m src
```

## Notes
- The app now updates file metadata in place using ExifTool instead of XMP sidecars.
- AI analysis is optional at runtime; the UI will stay usable even if the model stack is unavailable.
- Libraries are monitored passively while the desktop app is open, and changes are picked up automatically.
- Use `scripts/download_exiftool.py` to ensure a local ExifTool copy exists in the configured folder.
- Semantic search uses FAISS when available; if `faiss-cpu` is not installed, the app falls back to filename search only.
