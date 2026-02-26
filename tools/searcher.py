"""
Leon Code Searcher — Two-tier retrieval.

Usage:
    leon-search "authentication hook" --project Motorev --topk 12
    leon-search "useStore" --topk 5
    python -m tools.searcher "query" --project Motorev

Tier 1: ripgrep lexical search  (<300ms target)
Tier 2: ChromaDB vector search  (semantic)

Results merged, deduplicated by file, sorted by score.
"""

import argparse
import json
import logging
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("leon.searcher")

RAG_DB_DIR      = Path("data/rag_db")
STRUCTURED_LOG  = Path("logs_structured/search.jsonl")


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _log_search(query: str, project: str, tier: str, n: int, ms: float):
    STRUCTURED_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now().isoformat(),
        "event": "search",
        "query": query[:100],
        "project": project,
        "tier": tier,
        "results": n,
        "latency_ms": round(ms, 1),
    }
    try:
        with open(STRUCTURED_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


# ── Tier 1: ripgrep lexical ───────────────────────────────────────────────────

def search_lexical(query: str, project_path: str, topk: int = 12) -> list[dict]:
    """
    Fast lexical search via ripgrep. Target: <300ms.
    Returns list of {source, filepath, start_line, end_line, snippet, score}.
    """
    t0 = time.monotonic()

    try:
        subprocess.run(["rg", "--version"], capture_output=True, check=True, timeout=2)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        logger.warning("ripgrep (rg) not found — install: sudo apt install ripgrep")
        return []

    try:
        result = subprocess.run(
            [
                "rg",
                "--json",
                "--max-count", "3",          # Max 3 matches per file
                "--max-filesize", "500K",
                "--ignore-case",
                "--type-add", "src:*.{ts,tsx,js,jsx,py,sh,yaml,yml,md,conf,toml}",
                "--type", "src",
                "--glob", "!node_modules",
                "--glob", "!.next",
                "--glob", "!dist",
                "--glob", "!__pycache__",
                "--glob", "!venv",
                "-e", query,
            ],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except subprocess.TimeoutExpired:
        logger.warning("ripgrep timed out (>5s)")
        return []
    except Exception as e:
        logger.warning(f"ripgrep failed: {e}")
        return []

    matches = []
    seen_files: set[str] = set()

    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        if entry.get("type") != "match":
            continue

        data = entry.get("data", {})
        raw_path = data.get("path", {}).get("text", "")

        # Make path relative to project
        try:
            rel = str(Path(raw_path).relative_to(project_path))
        except ValueError:
            rel = raw_path

        line_no = data.get("line_number", 0)
        text = data.get("lines", {}).get("text", "").strip()

        # One result per file to keep output readable
        if rel in seen_files:
            continue
        seen_files.add(rel)

        matches.append({
            "source":     "lexical",
            "filepath":   rel,
            "start_line": max(1, line_no - 2),
            "end_line":   line_no + 2,
            "snippet":    text[:300],
            "score":      1.0,
        })

        if len(matches) >= topk:
            break

    ms = (time.monotonic() - t0) * 1000
    _log_search(query, project_path, "lexical", len(matches), ms)
    logger.debug(f"Lexical: {len(matches)} results in {ms:.0f}ms")
    return matches


# ── Tier 2: ChromaDB vector ───────────────────────────────────────────────────

def search_vector(query: str, project_name: str, topk: int = 8) -> list[dict]:
    """
    Semantic search via ChromaDB.
    Requires: chromadb + sentence-transformers (installed by upgrade script).
    Returns [] if not available — degrades gracefully.
    """
    t0 = time.monotonic()
    chroma_dir = RAG_DB_DIR / _slug(project_name) / "chroma"

    if not chroma_dir.exists():
        logger.debug(f"No vector index for {project_name} — run: leon-index --project {project_name}")
        return []

    try:
        import chromadb
        from chromadb.utils import embedding_functions

        client = chromadb.PersistentClient(path=str(chroma_dir))

        try:
            ef = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name="all-MiniLM-L6-v2"
            )
        except Exception:
            ef = embedding_functions.DefaultEmbeddingFunction()

        collection = client.get_or_create_collection(
            name="code_chunks", embedding_function=ef
        )

        count = collection.count()
        if count == 0:
            return []

        results = collection.query(
            query_texts=[query],
            n_results=min(topk, count),
        )

        matches = []
        docs      = results.get("documents",  [[]])[0]
        metas     = results.get("metadatas",  [[]])[0]
        distances = results.get("distances",  [[]])[0]

        for doc, meta, dist in zip(docs, metas, distances):
            score = max(0.0, 1.0 - dist / 2.0)   # cosine distance → similarity
            if score < 0.15:
                continue
            matches.append({
                "source":     "vector",
                "filepath":   meta.get("filepath", ""),
                "start_line": meta.get("start_line", 0),
                "end_line":   meta.get("end_line", 0),
                "snippet":    doc[:300],
                "score":      round(score, 3),
            })

        ms = (time.monotonic() - t0) * 1000
        _log_search(query, project_name, "vector", len(matches), ms)
        return matches

    except ImportError:
        logger.debug("chromadb not installed — vector search skipped")
        return []
    except Exception as e:
        logger.warning(f"Vector search failed: {e}")
        return []


# ── Two-tier merge ────────────────────────────────────────────────────────────

def search(
    query: str,
    project_name: str,
    project_path: str,
    topk: int = 12,
) -> list[dict]:
    """
    Combined two-tier search.
    Lexical results take priority; vector adds semantic matches not in lexical set.
    Final list sorted by score descending, deduplicated by filepath.
    """
    lexical = search_lexical(query, project_path, topk=topk)
    vector  = search_vector(query, project_name,  topk=topk)

    seen    = {r["filepath"] for r in lexical}
    merged  = list(lexical)
    for r in vector:
        if r["filepath"] not in seen:
            merged.append(r)
            seen.add(r["filepath"])

    merged.sort(key=lambda x: x["score"], reverse=True)
    return merged[:topk]


# ── Output formatter ──────────────────────────────────────────────────────────

def format_results(results: list[dict], project_name: str = "") -> str:
    if not results:
        return "  No results found."
    lines = []
    prefix = f"{project_name}/" if project_name else ""
    for i, r in enumerate(results, 1):
        tag   = f"[{r['source']}]"
        score = f"score={r['score']:.2f}"
        lines.append(
            f"\n  {i}. {prefix}{r['filepath']}:{r['start_line']}-{r['end_line']}  "
            f"{tag}  {score}"
        )
        for sl in r["snippet"].strip().split("\n")[:4]:
            lines.append(f"     {sl}")
    return "\n".join(lines)


# ── CLI entry point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Search Leon project codebases",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            '  leon-search "authentication hook" --project Motorev\n'
            '  leon-search "useStore" --topk 5\n'
            '  leon-search "API error" --project "Leon System"'
        ),
    )
    parser.add_argument("query",    help="Search query")
    parser.add_argument("--project", help="Project name (searches all if omitted)")
    parser.add_argument("--topk",  type=int, default=12, help="Max results (default 12)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    try:
        import yaml
        cfg_path = Path(__file__).parent.parent / "config" / "projects.yaml"
        all_projects = yaml.safe_load(cfg_path.read_text()).get("projects", [])
    except Exception as e:
        print(f"Could not load projects.yaml: {e}")
        sys.exit(1)

    if args.project:
        targets = [p for p in all_projects if p["name"].lower() == args.project.lower()]
        if not targets:
            names = [p["name"] for p in all_projects]
            print(f"Project '{args.project}' not found. Available: {', '.join(names)}")
            sys.exit(1)
    else:
        targets = [p for p in all_projects if Path(p.get("path", "")).exists()]

    all_results: list[dict] = []
    for p in targets:
        results = search(args.query, p["name"], p["path"], topk=args.topk)
        for r in results:
            r["project"] = p["name"]
        all_results.extend(results)

    all_results.sort(key=lambda x: x["score"], reverse=True)

    print(f"\nSearch: '{args.query}' — {len(all_results)} result(s)")
    for proj_name in {r.get("project", "") for r in all_results}:
        proj_results = [r for r in all_results if r.get("project") == proj_name]
        print(f"\n  [{proj_name}]")
        print(format_results(proj_results[:args.topk]))


if __name__ == "__main__":
    main()
