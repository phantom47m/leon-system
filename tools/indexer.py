"""
Leon Code Indexer — Two-tier RAG index builder.

Usage:
    leon-index --project Motorev
    leon-index --project "Leon System" --force
    python -m tools.indexer --project Motorev

Tier 1: File hash cache for incremental detection
Tier 2: ChromaDB + sentence-transformers (local, no API cost)

Database: data/rag_db/<project_slug>/
  chroma/          — ChromaDB persistent store
  file_hashes.json — Change detection cache
"""

import argparse
import hashlib
import json
import logging
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("leon.indexer")

# ── Config ────────────────────────────────────────────────────────────────────

SOURCE_EXTENSIONS = {
    ".ts", ".tsx", ".js", ".jsx",
    ".py",
    ".sh",
    ".yaml", ".yml",
    ".md",
    ".json",
    ".conf", ".toml",
}

SKIP_DIRS = {
    "node_modules", ".next", "dist", "build", "__pycache__",
    ".git", ".expo", "venv", ".venv", "coverage", ".nyc_output",
    "vendor", ".cache", "tmp", ".turbo", "stl_downloads",
    "agent_outputs", "task_briefs", "voice_cache",
}

SKIP_FILES = {
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
    "poetry.lock", "Pipfile.lock",
}

MAX_JSON_SIZE = 50_000   # Skip JSON files > 50KB
MAX_MD_SIZE  = 20_000    # Skip large markdown
CHUNK_SIZE   = 1200      # Target chars per chunk
RAG_DB_DIR   = Path("data/rag_db")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _should_skip(path: Path) -> bool:
    for part in path.parts:
        if part in SKIP_DIRS:
            return True
    if path.name in SKIP_FILES:
        return True
    try:
        size = path.stat().st_size
        if path.suffix == ".json" and size > MAX_JSON_SIZE:
            return True
        if path.suffix == ".md" and size > MAX_MD_SIZE:
            return True
        if size > 500_000:  # Skip any file > 500KB
            return True
    except OSError:
        pass
    return False


def _file_hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def _chunk_code(text: str, filepath: str) -> list[dict]:
    """
    Split source into chunks at function/class/export boundaries.
    Falls back to 40-line sliding window if no boundaries found.
    Each chunk includes: text, filepath, start_line, end_line.
    """
    lines = text.split("\n")

    boundary_re = re.compile(
        r"^(async\s+)?def\s+\w+|"           # Python function
        r"^class\s+\w+|"                    # Python class
        r"^export\s+(default\s+)?(function|class|const\s+\w+\s*=\s*(async\s+)?(\(|function))|"
        r"^function\s+\w+|"                 # JS function
        r"^const\s+\w+\s*=\s*(async\s+)?\(",  # Arrow function
        re.MULTILINE,
    )

    boundaries = [0]
    for m in boundary_re.finditer(text):
        ln = text[: m.start()].count("\n")
        if ln > boundaries[-1] + 3:  # Avoid duplicate adjacent boundaries
            boundaries.append(ln)
    boundaries.append(len(lines))

    chunks = []
    for i in range(len(boundaries) - 1):
        s, e = boundaries[i], boundaries[i + 1]
        chunk_text = "\n".join(lines[s:e])

        if len(chunk_text) > CHUNK_SIZE * 2:
            # Sub-chunk large blocks
            pos = s
            while pos < e:
                end = min(pos + 40, e)
                sub = "\n".join(lines[pos:end])
                if sub.strip():
                    chunks.append({"text": sub, "filepath": filepath,
                                   "start_line": pos + 1, "end_line": end})
                pos = end - 2  # 2-line overlap
        elif chunk_text.strip():
            chunks.append({"text": chunk_text, "filepath": filepath,
                           "start_line": s + 1, "end_line": e})

    return chunks


def _collect_files(project_path: Path) -> list[Path]:
    """Collect indexable files, respecting .gitignore via git ls-files."""
    files = []

    if (project_path / ".git").exists():
        try:
            r = subprocess.run(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                cwd=project_path, capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                for line in r.stdout.strip().split("\n"):
                    p = project_path / line.strip()
                    if p.exists() and p.suffix in SOURCE_EXTENSIONS and not _should_skip(p):
                        files.append(p)
                return files
        except Exception:
            pass  # Fall through to manual walk

    for ext in SOURCE_EXTENSIONS:
        for p in project_path.rglob(f"*{ext}"):
            if not _should_skip(p):
                files.append(p)
    return files


# ── Indexer class ─────────────────────────────────────────────────────────────

class CodeIndexer:
    """Manages the RAG index for a single project."""

    def __init__(self, project_name: str, project_path: str):
        self.project_name = project_name
        self.project_path = Path(project_path)
        self.db_dir = RAG_DB_DIR / _slug(project_name)
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self._hash_file = self.db_dir / "file_hashes.json"
        self._hashes: dict = self._load_hashes()
        self._collection = None

    def _load_hashes(self) -> dict:
        if self._hash_file.exists():
            try:
                return json.loads(self._hash_file.read_text())
            except Exception:
                pass
        return {}

    def _save_hashes(self, hashes: dict):
        self._hash_file.write_text(json.dumps(hashes, indent=2))

    def _get_collection(self):
        """Lazy-init ChromaDB collection. Returns None if chromadb not installed."""
        if self._collection is not None:
            return self._collection
        try:
            import chromadb
            from chromadb.utils import embedding_functions

            client = chromadb.PersistentClient(path=str(self.db_dir / "chroma"))

            try:
                ef = embedding_functions.SentenceTransformerEmbeddingFunction(
                    model_name="all-MiniLM-L6-v2"
                )
            except Exception:
                ef = embedding_functions.DefaultEmbeddingFunction()
                logger.info("sentence-transformers unavailable — using default embeddings")

            self._collection = client.get_or_create_collection(
                name="code_chunks",
                embedding_function=ef,
                metadata={"hnsw:space": "cosine"},
            )
            return self._collection
        except ImportError:
            logger.warning(
                "chromadb not installed — vector search disabled. "
                "Run: scripts/upgrade_autonomous.sh"
            )
            return None
        except Exception as e:
            logger.error(f"ChromaDB init failed: {e}")
            return None

    def index(self, force: bool = False) -> dict:
        """
        Incrementally index the project.
        Only re-indexes files whose hash has changed since last run.
        Returns stats dict.
        """
        files = _collect_files(self.project_path)
        collection = self._get_collection()

        stats = {
            "project": self.project_name,
            "files_scanned": len(files),
            "files_indexed": 0,
            "files_skipped": 0,
            "chunks_added": 0,
            "vector_db": collection is not None,
            "started_at": datetime.now().isoformat(),
        }

        new_hashes = {}
        all_chunks: list[dict] = []

        for filepath in files:
            rel = str(filepath.relative_to(self.project_path))
            fhash = _file_hash(filepath)
            new_hashes[rel] = fhash

            if not force and self._hashes.get(rel) == fhash:
                stats["files_skipped"] += 1
                continue

            try:
                text = filepath.read_text(errors="replace")
                chunks = _chunk_code(text, rel)
                all_chunks.extend(chunks)
                stats["files_indexed"] += 1
            except Exception as e:
                logger.warning(f"Could not read {rel}: {e}")

        if collection and all_chunks:
            batch_size = 50
            for i in range(0, len(all_chunks), batch_size):
                batch = all_chunks[i : i + batch_size]
                ids = [
                    hashlib.md5(f"{c['filepath']}:{c['start_line']}".encode()).hexdigest()
                    for c in batch
                ]
                try:
                    collection.upsert(
                        ids=ids,
                        documents=[c["text"] for c in batch],
                        metadatas=[
                            {
                                "filepath": c["filepath"],
                                "start_line": c["start_line"],
                                "end_line": c["end_line"],
                            }
                            for c in batch
                        ],
                    )
                    stats["chunks_added"] += len(batch)
                except Exception as e:
                    logger.error(f"ChromaDB upsert failed: {e}")

        self._hashes.update(new_hashes)
        self._save_hashes(self._hashes)

        stats["finished_at"] = datetime.now().isoformat()
        logger.info(
            f"Indexed {self.project_name}: {stats['files_indexed']} files, "
            f"{stats['chunks_added']} chunks, {stats['files_skipped']} unchanged"
        )
        return stats


# ── CLI entry point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Index a Leon project for RAG search",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  leon-index --project Motorev\n  leon-index --project Motorev --force",
    )
    parser.add_argument("--project", required=True, help="Project name from projects.yaml")
    parser.add_argument("--force", action="store_true", help="Force re-index all files")
    parser.add_argument("--all", action="store_true", help="Index all configured projects")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        import yaml
        cfg_path = Path(__file__).parent.parent / "config" / "projects.yaml"
        projects = yaml.safe_load(cfg_path.read_text()).get("projects", [])
    except Exception as e:
        print(f"Could not load projects.yaml: {e}")
        sys.exit(1)

    if args.all:
        targets = [p for p in projects if Path(p.get("path", "")).exists()]
    else:
        targets = [p for p in projects if p["name"].lower() == args.project.lower()]
        if not targets:
            names = [p["name"] for p in projects]
            print(f"Project '{args.project}' not found. Available: {', '.join(names)}")
            sys.exit(1)

    for project in targets:
        print(f"\nIndexing: {project['name']} ({project['path']})")
        indexer = CodeIndexer(project["name"], project["path"])
        stats = indexer.index(force=args.force)
        print(f"  Files scanned : {stats['files_scanned']}")
        print(f"  Files indexed : {stats['files_indexed']}  (changed)")
        print(f"  Files skipped : {stats['files_skipped']}  (unchanged)")
        print(f"  Chunks added  : {stats['chunks_added']}")
        print(f"  Vector DB     : {'✓' if stats['vector_db'] else '✗ (chromadb not installed)'}")


if __name__ == "__main__":
    main()
