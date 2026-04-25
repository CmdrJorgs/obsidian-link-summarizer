#!/usr/bin/env python3
"""
Recursively reads every .md file in an inbox directory,
extracts all URLs, and summarizes each one using Ollama (gemma4:e4b).
"""

import os
import re
import subprocess
import sys
import time
from urllib.parse import urlparse


# ── Configuration ────────────────────────────────────────────────
INPUT_DIR  = "/home/djorgs/Documents/Obsidian/David Second Brain/inbox/"
OUTPUT_DIR = "/home/djorgs/Documents/Obsidian/David Second Brain/bin/inbox-processed/"
MODEL      = "gemma4:e4b"
OLLAMA_CMD = ["ollama", "run", MODEL]  # streaming model; use "ollama serve" + API for non-terminal mode

# Regex that matches URLs
URL_RE = re.compile(
    r'https?://[^\s<>"\'\)\]\}]+'
)


# ── Helpers ──────────────────────────────────────────────────────
def collect_urls_from_file(filepath: str) -> list[str]:
    """Return all unique URLs found in a markdown file."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except (OSError, IOError):
        print(f"  ⚠  Could not read {filepath}")
        return []

    found = URL_RE.findall(text)
    # Clean trailing punctuation that is not part of the URL
    cleaned = []
    for u in found:
        u = u.rstrip(")»›").rstrip(".,;:!?)]")
        # Basic sanity: URL must still contain ://
        if "://" in u:
            cleaned.append(u)
    return list(dict.fromkeys(cleaned))  # deduplicate while preserving order


def collect_all_urls(input_dir: str) -> list[str]:
    """Walk the directory tree and collect every unique URL from .md files."""
    urls: list[str] = []
    seen: set[str] = set()

    for root, _dirs, files in os.walk(input_dir):
        for fname in files:
            if not fname.lower().endswith(".md"):
                continue
            filepath = os.path.join(root, fname)
            for url in collect_urls_from_file(filepath):
                if url not in seen:
                    seen.add(url)
                    urls.append(url)
    return urls


def summarize_url(url: str) -> str | None:
    """Use ollama (non-streaming via API) to summarize a URL."""
    prompt = (
        f"Summarize the content of this URL in a concise paragraph. "
        f"Return only the summary, no intro, no extra text.\n\n"
        f"URL: {url.encode('utf-8')}"
    )

    # Use ollama API (non-streaming) instead of "ollama run" to avoid terminal/tty issues
    api_payload = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
    }
    try:
        import http.client
        conn = http.client.HTTPConnection("localhost", 11434, timeout=120)
        conn.request("POST", "/api/generate", body=api_payload.__str__().replace("model", '"model"').replace("prompt", '"prompt"').replace("stream", '"stream"'), headers={"Content-Type": "application/json"})
        # Simpler approach: dump JSON manually
        import json
        conn2 = http.client.HTTPConnection("localhost", 11434, timeout=120)
        conn2.request(
            "POST",
            "/api/generate",
            body=json.dumps(api_payload),
            headers={"Content-Type": "application/json"},
        )
        resp = conn2.getresponse()
        if resp.status == 200:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("response", "").strip()
        else:
            print(f"  ✗  API error ({resp.status}) for {url}")
            return None
    except Exception as e:
        print(f"  ✗  Ollama API error for {url}: {e}")
        return None


def safe_filename(url: str) -> str:
    """Turn a URL into a safe filesystem-friendly filename."""
    parsed = urlparse(url)
    slug = parsed.netloc + parsed.path
    slug = re.sub(r"[^\w\s-]", "", slug).strip().lower()
    slug = re.sub(r"[-\s]+", "-", slug)
    slug = slug[:180]  # prevent filename too long
    return slug or "untitled"


def write_summary(output_dir: str, url: str, summary: str, source_file: str | None = None) -> str:
    """Write the summary to a markdown file in the output directory."""
    os.makedirs(output_dir, exist_ok=True)
    fname = safe_filename(url) + ".md"
    outpath = os.path.join(output_dir, fname)

    body = ""
    if source_file:
        rel_source = os.path.relpath(source_file, input_dir_global)
        body += f"> Summarized from: [{rel_source}]({rel_source})\n\n"

    body += f"- **URL**: {url}\n"
    body += f"- **Date**: {time.strftime('%Y-%m-%d %H:%M')}\n"
    body += f"- **Model**: {MODEL}\n\n"
    body += "---\n\n"
    body += summary + "\n"

    with open(outpath, "w", encoding="utf-8") as f:
        f.write(body)
    return outpath


# ── Globals ──────────────────────────────────────────────────────
input_dir_global = INPUT_DIR  # needed by write_summary closure


# ── Main ─────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  Obsidian Link Summarizer")
    print("=" * 60)

    # 1. Collect URLs
    print(f"\n📂 Scanning {INPUT_DIR} for .md files...")
    urls = collect_all_urls(INPUT_DIR)

    if not urls:
        print("No URLs found. Nothing to do.")
        sys.exit(0)

    print(f"🔗 Found {len(urls)} unique URL(s).")

    # Check Ollama connectivity
    try:
        import json
        import http.client
        conn = http.client.HTTPConnection("localhost", 11434, timeout=120)
        conn.request("GET", "/api/tags")
        resp = conn.getresponse()
        if resp.status == 200:
            available = json.loads(resp.read().decode("utf-8")).get("models", [])
            model_names = [m["name"] for m in available]
            if MODEL not in model_names:
                print(f"\n⚠  WARNING: model '{MODEL}' not found in Ollama.")
                print(f"   Available models: {', '.join(model_names)}")
                ans = input("  Continue anyway? (y/N): ").strip().lower()
                if ans != "y":
                    sys.exit(0)
            else:
                print(f"✅  Model '{MODEL}' is available.")
        else:
            print(f"⚠  Could not verify Ollama connectivity (status {resp.status}). Proceeding anyway.")
    except Exception as e:
        print(f"⚠  Could not verify Ollama connectivity: {e}. Proceeding anyway.")

    # 2. Process each URL
    output_dir = OUTPUT_DIR
    os.makedirs(output_dir, exist_ok=True)

    total = len(urls)
    processed = 0
    skipped = 0

    # Build lookup: url -> source file (first one found)
    url_to_source: dict[str, str] = {}
    for root, _dirs, files in os.walk(INPUT_DIR):
        for fname in files:
            if not fname.lower().endswith(".md"):
                continue
            fp = os.path.join(root, fname)
            for url in collect_urls_from_file(fp):
                if url not in url_to_source:
                    url_to_source[url] = fp

    for idx, url in enumerate(urls, start=1):
        print(f"\n[{idx}/{total}] ({idx}/{total}) Processing: {url[:100]}{'...' if len(url) > 100 else ''}")

        # Check if already processed
        existing = os.path.join(output_dir, safe_filename(url) + ".md")
        if os.path.exists(existing):
            print(f"  ⏭  Already exists: {existing}")
            skipped += 1
            continue

        summary = summarize_url(url)
        if summary:
            outpath = write_summary(output_dir, url, summary, source_file=url_to_source.get(url))
            print(f"  ✅  Saved to: {outpath}")
        else:
            print(f"  ✗  Failed to summarize")

        processed += 1
        # Small delay to avoid overwhelming the model
        time.sleep(1)

    print("\n" + "=" * 60)
    print(f"  Done! Processed: {processed}, Skipped: {skipped}")
    print("=" * 60)


if __name__ == "__main__":
    main()
