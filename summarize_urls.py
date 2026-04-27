#!/usr/bin/env python3
"""
Recursively reads every .md file in an inbox directory,
extracts all URLs, scrapes their HTML content,
and summarizes each one using Ollama (gemma4:e4b).
"""

import argparse
import http.client
import json
import os
import re
import sys
import time
import urllib.request
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from dotenv import load_dotenv

# import .env variables
load_dotenv()
OLLAMA_API_KEY = os.getenv('OLLAMA_API_KEY')
SELENIUM_DOMAINS = {d.strip() for d in (os.getenv('SELENIUM_DOMAINS') or '').split(',') if d.strip()}
INPUT_DIR  = os.getenv('INPUT_DIR')
OUTPUT_DIR = os.getenv('OUTPUT_DIR')
MODEL      = os.getenv('MODEL')
OLLAMA_CMD = os.getenv('OLLAMA_CMD')
NUMBER_TO_FETCH = os.getenv('NUMBER_TO_FETCH')

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


def collect_all_urls(input_dir: str, max_files: int = int(NUMBER_TO_FETCH or 0)) -> list[str]:
    """Walk the directory tree and collect URLs from the most recently modified .md files."""
    md_files = []
    for root, _dirs, files in os.walk(input_dir):
        for fname in files:
            if not fname.lower().endswith(".md"):
                continue
            filepath = os.path.join(root, fname)
            md_files.append((os.path.getmtime(filepath), filepath))

    md_files.sort(reverse=True)
    recent_files = [fp for _, fp in (md_files if not max_files else md_files[:max_files])]

    urls: list[str] = []
    seen: set[str] = set()
    for filepath in recent_files:
        for url in collect_urls_from_file(filepath):
            if url not in seen:
                seen.add(url)
                urls.append(url)
    return urls

def collect_file_urls(filepath: str) -> list[str]:
    """Collect URLs from the specified .md file in the --file argument."""
    md_files = []
    md_files.append((os.path.getmtime(filepath), filepath))

    urls: list[str] = []
    seen: set[str] = set()
    for url in collect_urls_from_file(filepath):
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def clean_html(html_content: str) -> str:
    """Extract readable text from HTML, removing scripts/styles."""
    try:
        soup = BeautifulSoup(html_content, "html.parser")
        # Remove script and style elements
        for script in soup(["script", "style", "header", "footer", "nav", "aside"]):
            script.decompose()
        # Get text
        text = soup.get_text(separator='\n')
        # Break into lines and remove leading and trailing white space
        lines = (line.strip() for line in text.splitlines())
        # Break multi-headlines into a line each
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        # Drop blank lines
        text = '\n'.join(chunk for chunk in chunks if chunk)
        return text
    except Exception:
        return html_content[:2000]  # Fallback if parsing fails


def extract_title(html_content: str) -> str:
    """Extract the <title> tag text from HTML."""
    try:
        soup = BeautifulSoup(html_content, "html.parser")
        tag = soup.find("title")
        return tag.get_text().strip() if tag else ""
    except Exception:
        return ""


def ollama_generate(prompt: str) -> str | None:
    """Send a prompt to the local Ollama API and return the response text."""
    try:
        payload = {"model": MODEL, "prompt": prompt, "stream": False}
        conn = http.client.HTTPConnection("localhost", 11434, timeout=120)
        conn.request("POST", "/api/generate", body=json.dumps(payload),
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        if resp.status == 200:
            return json.loads(resp.read().decode("utf-8")).get("response", "").strip()
        print(f"  ✗  Ollama API error (status {resp.status})")
        return None
    except Exception as e:
        print(f"  ✗  Ollama API error: {e}")
        return None


def fetch_with_selenium(url: str) -> str:
    """Fetch page HTML using a real Chrome browser via Selenium."""
    

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1280,900")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    driver = webdriver.Chrome(options=opts)
    try:
        driver.get(url)
        # Wait for body to be present and allow JS to render
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        time.sleep(2)  # let dynamic content settle
        return driver.page_source
    finally:
        driver.quit()


def needs_selenium(url: str) -> bool:
    return urlparse(url).netloc in SELENIUM_DOMAINS


def summarize_url(url: str) -> dict | None:
    """Fetch HTML from URL, then return title, summary, and tags via Ollama."""
    # 1. Fetch HTML
    html_content = None
    if needs_selenium(url):
        print(f"  🌐  Using Selenium for {urlparse(url).netloc}")
        try:
            html_content = fetch_with_selenium(url)
        except Exception as e:
            print(f"  ⚠  Could not fetch {url}: {e}")
            return None
    else:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=30) as response:
                html_content = response.read().decode('utf-8', errors='ignore')
        except Exception as e:
            print(f"  ⚠  urllib failed ({e}), retrying with Selenium...")
            try:
                html_content = fetch_with_selenium(url)
            except Exception as e2:
                print(f"  ⚠  Selenium also failed for {url}: {e2}")
                return None

    # 2. Extract title and clean body text
    title = extract_title(html_content)
    text_content = clean_html(html_content)
    if len(text_content) > 12000:
        text_content = text_content[:12000] + "\n... [truncated] ..."

    # 3. Generate summary
    summary = ollama_generate(
        f"Summarize the following content in a concise paragraph. "
        f"Return only the summary, no intro, no extra text.\n\n"
        f"---\n{text_content}\n---"
    )
    if not summary:
        return None

    # 4. Generate tags
    tags_raw = ollama_generate(
        f"Generate 3-7 concise lowercase tags for the following content. "
        f"Return only a comma-separated list of tags, no intro, no extra text.\n\n"
        f"---\n{text_content}\n---"
    )
    tags = [
        re.sub(r"[^\w-]", "-", tag.strip().lower())
        for tag in tags_raw.split(",")
        if tag.strip()
    ]

    return {"title": title, "summary": summary, "tags": tags}


def safe_filename(url: str) -> str:
    """Turn a URL into a safe filesystem-friendly filename."""
    parsed = urlparse(url)
    slug = parsed.netloc + parsed.path
    slug = re.sub(r"[^\w\s-]", "_", slug).strip().lower()
    slug = re.sub(r"[-\s]+", "-", slug)
    slug = slug[:180]  # prevent filename too long
    return slug or "untitled"


def write_summary(output_dir: str, url: str, title: str, summary: str, tags: list[str]) -> str:
    """Write the summary to a markdown file with YAML frontmatter."""
    os.makedirs(output_dir, exist_ok=True)
    fname = safe_filename(url) + ".md"
    outpath = os.path.join(output_dir, fname)

    safe_title = title.replace('"', '\\"') if title else safe_filename(url)
    tags_yaml = "\n".join(f"  - {t}" for t in tags) if tags else "  []"
    frontmatter = (
        f"---\n"
        f'title: "{safe_title}"\n'
        f"source: {url}\n"
        f"created: {time.strftime('%Y-%m-%d')}\n"
        f"tags:\n{tags_yaml}\n"
        f"---\n\n"
    )

    with open(outpath, "w", encoding="utf-8") as f:
        f.write(frontmatter + summary + "\n")
    return outpath


# ── Main ─────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="Process all inbox files, ignoring NUMBER_TO_FETCH")
    parser.add_argument("--file", type=str, required=False, help="Path to the input file if you only want to process a single file, ignoring NUMBER_TO_FETCH")
    args = parser.parse_args()

    max_files = 0 if args.all else int(NUMBER_TO_FETCH or 0)
    file_bool = True if args.file else False

    print("=" * 60)
    print("  Obsidian Link Summarizer")
    print("=" * 60)

    # 1. Collect URLs
    if file_bool:
        scope = args.file
        print(f"\n📄 Scanning {scope}...")
        urls = collect_file_urls(args.file)
    else:
        scope = "all" if not max_files else f"{max_files} most recent"
        print(f"\n📂 Scanning {INPUT_DIR} for .md files ({scope})...")
        urls = collect_all_urls(INPUT_DIR, max_files=max_files)
    
    

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

    for idx, url in enumerate(urls, start=1):
        print(f"\n[{idx}/{total}] ({idx}/{total}) Processing: {url[:100]}{'...' if len(url) > 100 else ''}")

        # Check if already processed
        existing = os.path.join(output_dir, safe_filename(url) + ".md")
        if os.path.exists(existing):
            print(f"  ⏭  Already exists: {existing}")
            skipped += 1
            continue

        result = summarize_url(url)
        if result:
            outpath = write_summary(output_dir, url, result["title"], result["summary"], result["tags"])
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
