"""Headless render check for vault-explorer.html.

Loads the file in chromium, captures console messages and page errors,
and prints what actually showed up vs what stayed at the placeholder.
"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

URL = "file://" + str(Path("wiki/meta/vault-explorer.html").resolve())

errors: list[str] = []
console_msgs: list[str] = []


def on_console(msg):
    if msg.type in ("error", "warning"):
        console_msgs.append(f"[{msg.type}] {msg.text}")


def on_pageerror(exc):
    errors.append(f"PAGEERROR: {exc}")


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.on("console", on_console)
    page.on("pageerror", on_pageerror)
    page.goto(URL)
    page.wait_for_load_state("networkidle", timeout=10000)
    page.wait_for_timeout(1000)

    # Switch to Maps tab so the sankey/treemap canvases are visible (Chart.js
    # skips drawing while parents are display:none).
    page.click('.tab-btn[data-panel="maps"]')
    page.wait_for_timeout(500)

    checks = {
        "health value":          "document.querySelector('#health-score .value').textContent.trim()",
        "hero pages":            "document.querySelector('#hero-pages-value').textContent.trim()",
        "dist-types canvas":     "document.querySelector('#dist-types').width",
        "dist-domains rendered": "document.querySelector('#dist-domains').width > 0",
        "wordcloud children":    "document.querySelector('#map-wordcloud').children.length",
        "one-line text":         "document.querySelector('#one-line').textContent.trim().slice(0, 80)",
        "pulse-last text":       "document.querySelector('#pulse-last-turn').textContent.trim().slice(0, 80)",
        "diff-history items":    "document.querySelector('#diff-history-list').children.length",
        "iframe-umap src":       "(document.querySelector('#iframe-umap').src || '').slice(-60)",
        "iframe-graph src":      "(document.querySelector('#iframe-graph').src || '').slice(-60)",
        "sankey canvas drawn":   "document.querySelector('#map-sankey') ? document.querySelector('#map-sankey').width > 0 : 'no canvas'",
        "treemap canvas drawn":  "document.querySelector('#map-treemap') ? document.querySelector('#map-treemap').width > 0 : 'no canvas'",
        "insight-umap text":     "document.querySelector('#insight-umap').textContent.trim().slice(0, 80)",
    }
    print("=== Render checks ===")
    for label, expr in checks.items():
        try:
            v = page.evaluate(expr)
        except Exception as e:
            v = f"<error: {e}>"
        print(f"  {label}: {v}")

    print()
    print("=== Page errors ===")
    for e in errors:
        print(e)
    if not errors:
        print("  (none)")

    print()
    print("=== Console (errors/warnings) ===")
    for m in console_msgs:
        print(" ", m)
    if not console_msgs:
        print("  (none)")

    browser.close()
