# Obsidian Link Summarizer

Scans your Obsidian inbox for markdown files, extracts every URL, scrapes the page content, and writes a summarized note with YAML frontmatter using a local Ollama model.

## How it works

1. Walks `INPUT_DIR` and collects URLs from the most recently modified `.md` files (controlled by `NUMBER_TO_FETCH`)
2. Fetches each page — using `urllib` by default, falling back to Selenium for sites that block headless requests (e.g. Reddit), or for any domain listed in `SELENIUM_DOMAINS`
3. Sends the cleaned page text to Ollama twice: once for a summary paragraph and once for a tag list
4. Writes a `.md` file to `OUTPUT_DIR` with YAML frontmatter (`title`, `source`, `created`, `tags`) and the summary as the body
5. Skips URLs whose output file already exists

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com) running locally on port 11434 with your chosen model pulled
- Google Chrome + ChromeDriver on `PATH` (for Selenium fallback)

Install Python dependencies:

```bash
pip install -r requirements.txt
```

## Configuration

Copy `.env.example` to `.env` and fill in the values:

```dotenv
INPUT_DIR=/path/to/obsidian/inbox/
OUTPUT_DIR=/path/to/obsidian/output/
MODEL=gemma4:e4b
NUMBER_TO_FETCH=5
SELENIUM_DOMAINS=reddit.com,old.reddit.com,twitter.com,x.com
OLLAMA_API_KEY=your_key_here
```

| Variable          | Description                                                                 |
|-------------------|-----------------------------------------------------------------------------|
| `INPUT_DIR`       | Directory to scan for `.md` files                                           |
| `OUTPUT_DIR`      | Directory where summary notes are written                                   |
| `MODEL`           | Ollama model name (e.g. `gemma4:e4b`, `llama3`)                             |
| `NUMBER_TO_FETCH` | How many of the most recently modified files to scan (`0` = all)            |
| `SELENIUM_DOMAINS`| Comma-separated list of domains that always use Selenium                    |
| `OLLAMA_API_KEY`  | API key for Ollama (if authentication is enabled)                           |

## Usage

Process the N most recent inbox files (per `NUMBER_TO_FETCH`):

```bash
python summarize_urls.py
```

Override `NUMBER_TO_FETCH` and process every file in the inbox:

```bash
python summarize_urls.py --all
```

## Output format

Each URL produces a `.md` file named after the URL slug:

```markdown
---
title: "Page Title Here"
source: https://example.com/article
created: 2026-04-24
tags:
  - technology
  - open-source
  - python
---

A concise paragraph summarizing the page content...
```
