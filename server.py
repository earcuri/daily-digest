#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["markdown"]
# ///
"""
Daily Digest Server — Dashboard UI

A config-driven web dashboard for viewing daily markdown digests.
Set DIGEST_DIR to point at your directory of YYYY-MM-DD.md files.
"""

import re
import json
import sys
import time
import datetime
import calendar as cal_mod
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from urllib.request import urlopen, Request
from urllib.error import URLError
import os
import markdown


# ---------------------------------------------------------------------------
# .env file loading (no external dependency needed)
# ---------------------------------------------------------------------------
def _load_dotenv(path: Path = None):
    """Load .env file into os.environ if it exists. Does not override existing vars."""
    if path is None:
        path = Path(__file__).parent / ".env"
    if not path.is_file():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # Strip matching quotes
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv()

# ---------------------------------------------------------------------------
# Configuration (all from environment variables)
# ---------------------------------------------------------------------------
DIGEST_DIR = Path(os.environ["DIGEST_DIR"]) if os.environ.get("DIGEST_DIR") else None
PORT = int(os.environ.get("DIGEST_PORT", "10001"))
HOST = os.environ.get("DIGEST_HOST", "127.0.0.1")
USER_NAME = os.environ.get("DIGEST_USER_NAME", "")
APP_NAME = os.environ.get("DIGEST_APP_NAME", "Daily Digest")
NAV_LINKS = json.loads(os.environ.get("DIGEST_NAV_LINKS", "[]"))  # [{"label":"X","url":"http://..."}]

SECTION_ICONS = {
    "weather": "\u2600\ufe0f",
    "career": "\U0001f4bc",
    "boxing": "\U0001f94a",
    "technology": "\U0001f4bb",
    "news": "\U0001f4f0",
    "miscellaneous": "\U0001f4cb",
    "calendar": "\U0001f4c5",
    "other": "\U0001f4c4",
}

SECTION_ACCENTS = {
    "career": "#7aa2f7",
    "boxing": "#f7768e",
    "technology": "#9ece6a",
    "news": "#e0af68",
    "miscellaneous": "#bb9af7",
    "calendar": "#ff9e64",
    "weather": "#e0af68",
    "other": "#666",
}

WMO_ICONS = {
    0: "\u2600\ufe0f", 1: "\U0001f324\ufe0f", 2: "\u26c5", 3: "\u2601\ufe0f",
    45: "\U0001f32b\ufe0f", 48: "\U0001f32b\ufe0f",
    51: "\U0001f326\ufe0f", 53: "\U0001f326\ufe0f", 55: "\U0001f327\ufe0f",
    61: "\U0001f327\ufe0f", 63: "\U0001f327\ufe0f", 65: "\U0001f327\ufe0f",
    71: "\U0001f328\ufe0f", 73: "\U0001f328\ufe0f", 75: "\u2744\ufe0f",
    77: "\U0001f328\ufe0f",
    80: "\U0001f326\ufe0f", 81: "\U0001f327\ufe0f", 82: "\U0001f327\ufe0f",
    85: "\U0001f328\ufe0f", 86: "\u2744\ufe0f",
    95: "\u26c8\ufe0f", 96: "\u26c8\ufe0f", 99: "\u26c8\ufe0f",
}

WMO_DESCRIPTIONS = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Freezing fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Light showers", 81: "Showers", 82: "Heavy showers",
    85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm w/ hail", 99: "Severe thunderstorm",
}

# ---------------------------------------------------------------------------
# Weather API (cached)
# ---------------------------------------------------------------------------

_weather_cache: dict = {"forecast": None, "alerts": None, "ts": 0}
CACHE_TTL = 3600  # 1 hour

LAT = float(os.environ.get("DIGEST_WEATHER_LAT", "0"))
LON = float(os.environ.get("DIGEST_WEATHER_LON", "0"))
WEATHER_ENABLED = bool(os.environ.get("DIGEST_WEATHER_LAT"))


def fetch_weather_forecast() -> dict | None:
    if not WEATHER_ENABLED:
        return None
    now = time.time()
    if _weather_cache["forecast"] and (now - _weather_cache["ts"]) < CACHE_TTL:
        return _weather_cache["forecast"]
    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={LAT}&longitude={LON}"
            f"&daily=temperature_2m_max,temperature_2m_min,weathercode,"
            f"windspeed_10m_max,precipitation_sum,uv_index_max"
            f"&temperature_unit=fahrenheit&timezone=America/Denver"
            f"&forecast_days=10"
        )
        req = Request(url, headers={"User-Agent": "DailyDigest/1.0"})
        with urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        _weather_cache["forecast"] = data
        _weather_cache["ts"] = now
        return data
    except (URLError, json.JSONDecodeError, OSError):
        return _weather_cache.get("forecast")


def fetch_weather_alerts() -> list:
    if not WEATHER_ENABLED:
        return []
    now = time.time()
    if _weather_cache["alerts"] is not None and (now - _weather_cache["ts"]) < CACHE_TTL:
        return _weather_cache["alerts"]
    try:
        url = f"https://api.weather.gov/alerts/active?point={LAT},{LON}"
        req = Request(url, headers={
            "User-Agent": "(DailyDigest, github.com/earcuri/daily-digest)",
            "Accept": "application/geo+json",
        })
        with urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        alerts = []
        for feature in data.get("features", []):
            props = feature.get("properties", {})
            alerts.append({
                "event": props.get("event", "Unknown"),
                "headline": props.get("headline", ""),
                "severity": props.get("severity", "Unknown"),
                "description": props.get("description", "")[:500],
                "expires": props.get("expires", ""),
            })
        _weather_cache["alerts"] = alerts
        return alerts
    except (URLError, json.JSONDecodeError, OSError):
        return _weather_cache.get("alerts") or []


# ---------------------------------------------------------------------------
# HTML Templates
# ---------------------------------------------------------------------------

PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title><!--TITLE--></title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🐾</text></svg>">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html { font-size: 16px; scroll-behavior: smooth; }
body {
  background: #0a0a0a; color: #e0e0e0;
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  line-height: 1.65; overflow-x: hidden;
}
a { color: #7aa2f7; text-decoration: none; }
a:hover { color: #9bb8fa; }
::selection { background: rgba(122,162,247,0.25); }

/* ── Hero ── */
.hero {
  height: 100vh; display: flex; flex-direction: column;
  justify-content: center; padding: 0 10vw;
  position: relative; overflow: hidden;
}
.hero::before {
  content: ''; position: absolute; width: 700px; height: 700px;
  background: radial-gradient(circle, rgba(122,162,247,0.1), transparent 70%);
  top: -150px; right: -100px; border-radius: 50%; pointer-events: none;
}
.hero::after {
  content: ''; position: absolute; width: 500px; height: 500px;
  background: radial-gradient(circle, rgba(255,158,100,0.06), transparent 70%);
  bottom: -100px; left: -50px; border-radius: 50%; pointer-events: none;
}
.hero-content { position: relative; z-index: 1; }
.greeting-pre {
  font-size: clamp(1.5rem, 3vw, 2.2rem); font-weight: 300;
  color: #888; margin-bottom: -0.2em;
}
.greeting-name {
  font-size: clamp(3rem, 7vw, 6rem); font-weight: 700;
  color: #f0f0f0; letter-spacing: -0.03em; line-height: 1.1;
}
.hero-date {
  font-size: clamp(0.85rem, 1.5vw, 1.1rem); color: #555;
  margin-top: 1rem; font-weight: 400; letter-spacing: 0.5px;
}
.hero.fade-out {
  opacity: 0 !important; transform: translateY(-30px) !important;
  transition: opacity 0.6s ease, transform 0.6s ease;
  pointer-events: none;
}
.hero.collapsed { display: none; }

/* ── Top Bar ── */
.topbar {
  position: sticky; top: 0; z-index: 100;
  background: rgba(10,10,10,0.92); backdrop-filter: blur(12px);
  border-bottom: 1px solid #1a1a1a;
  padding: 0 2rem; height: 56px;
  display: flex; align-items: center; justify-content: space-between;
}
.brand {
  font-size: 0.85rem; font-weight: 600; letter-spacing: 0.5px;
  color: #e0e0e0; white-space: nowrap; cursor: pointer;
}
.brand-dim { color: #555; font-weight: 400; }
.weather-bar {
  display: flex; align-items: center; gap: 0.6rem;
  overflow-x: auto; scrollbar-width: none; padding: 0 1rem;
  flex: 1; justify-content: center; max-width: 600px;
}
.weather-bar::-webkit-scrollbar { display: none; }
.weather-chip {
  display: flex; align-items: center; gap: 0.25rem;
  font-size: 0.8rem; white-space: nowrap; cursor: default;
  padding: 0.2rem 0.5rem; border-radius: 20px;
  transition: background 0.2s;
}
.weather-chip:hover { background: rgba(255,255,255,0.05); }
.chip-icon { font-size: 0.95rem; }
.chip-temp { font-weight: 600; font-size: 0.8rem; }
.chip-time { font-size: 0.65rem; color: #555; margin-left: 0.1rem; }
.nav-right {
  display: flex; align-items: center; gap: 0.5rem;
  font-size: 0.8rem; white-space: nowrap;
}
.nav-btn {
  padding: 0.3rem 0.7rem; border-radius: 6px;
  color: #999; transition: all 0.2s;
}
.nav-btn:hover { color: #e0e0e0; background: rgba(255,255,255,0.05); }
.nav-arrow {
  width: 32px; height: 32px; display: inline-flex;
  align-items: center; justify-content: center;
  border-radius: 6px; color: #666; transition: all 0.2s;
}
.nav-arrow:hover { color: #e0e0e0; background: rgba(255,255,255,0.05); }
.nav-arrow.disabled { opacity: 0.2; pointer-events: none; }

/* ── Dashboard Grid ── */
.dashboard-view {
  max-width: 1100px; margin: 0 auto;
  padding: 2rem 2rem 4rem;
}
.dash-title {
  font-size: 0.65rem; font-weight: 600; letter-spacing: 2px;
  color: #333; margin-bottom: 1.5rem; text-transform: uppercase;
}
.card-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 1rem;
}
.dash-card {
  background: #111; border: 1px solid #1a1a1a;
  border-radius: 12px; padding: 1.25rem 1.25rem 1rem;
  cursor: pointer; transition: all 0.3s ease;
  position: relative; overflow: hidden;
  border-left: 3px solid var(--card-accent, #666);
}
.dash-card::before {
  content: ''; position: absolute; inset: 0;
  background: radial-gradient(circle at 20% 20%, var(--card-accent, #666), transparent 70%);
  opacity: 0; transition: opacity 0.3s ease;
  pointer-events: none;
}
.dash-card:hover {
  transform: translateY(-3px);
  border-color: var(--card-accent, #666);
  box-shadow: 0 8px 32px -8px color-mix(in srgb, var(--card-accent) 25%, transparent);
}
.dash-card:hover::before { opacity: 0.06; }
.card-header {
  display: flex; align-items: center; gap: 0.6rem;
  margin-bottom: 0.6rem;
}
.card-icon { font-size: 1.3rem; }
.card-name {
  font-size: 0.9rem; font-weight: 600; color: #e0e0e0;
}
.card-preview {
  font-size: 0.8rem; color: #666; line-height: 1.5;
  display: -webkit-box; -webkit-line-clamp: 3;
  -webkit-box-orient: vertical; overflow: hidden;
}
.card-widget {
  display: inline-flex; align-items: center; gap: 0.3rem;
  margin-top: 0.75rem; padding: 0.25rem 0.6rem;
  background: rgba(255,255,255,0.04); border-radius: 6px;
  font-size: 0.7rem; color: #888; border: 1px solid #222;
  transition: all 0.2s;
}
.card-widget:hover { background: rgba(255,255,255,0.08); color: #bbb; }

/* Weather card spans full width */
.dash-card.weather-card {
  grid-column: 1 / -1;
}
.weather-card-grid {
  display: flex; gap: 0.5rem; margin-top: 0.5rem;
  overflow-x: auto; scrollbar-width: none;
}
.weather-card-grid::-webkit-scrollbar { display: none; }
.wx-mini {
  text-align: center; min-width: 64px; flex-shrink: 0;
  padding: 0.4rem; background: #0d0d0d; border-radius: 8px;
}
.wx-mini-day { font-size: 0.6rem; color: #555; font-weight: 600; text-transform: uppercase; }
.wx-mini-icon { font-size: 1.1rem; margin: 0.1rem 0; }
.wx-mini-temp { font-size: 0.75rem; font-weight: 600; }
.wx-mini-lo { font-size: 0.65rem; color: #444; }

/* ── Section View ── */
.section-view {
  display: none; max-width: 900px; margin: 0 auto;
  padding: 0 2rem 4rem;
}
.section-view.active { display: block; animation: fadeUp 0.3s ease; }

@keyframes fadeUp {
  from { opacity: 0; transform: translateY(12px); }
  to { opacity: 1; transform: translateY(0); }
}

.section-toolbar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 1rem 0; margin-bottom: 0.5rem;
  border-bottom: 1px solid #1a1a1a;
}
.back-btn {
  background: none; border: 1px solid #222; color: #888;
  padding: 0.4rem 0.9rem; border-radius: 6px; cursor: pointer;
  font-family: inherit; font-size: 0.8rem; transition: all 0.2s;
}
.back-btn:hover { color: #e0e0e0; border-color: #444; background: rgba(255,255,255,0.03); }
.section-nav-bar {
  display: flex; align-items: center; gap: 0.5rem;
}
.sec-nav-btn {
  background: none; border: none; color: #555; cursor: pointer;
  font-size: 1rem; padding: 0.3rem; border-radius: 4px;
  transition: all 0.2s; font-family: inherit;
}
.sec-nav-btn:hover { color: #e0e0e0; background: rgba(255,255,255,0.05); }
.sec-nav-btn.disabled { opacity: 0.2; pointer-events: none; }
.section-nav-title {
  font-size: 0.85rem; font-weight: 600; color: #e0e0e0;
  display: flex; align-items: center; gap: 0.5rem;
}
.section-nav-icon { font-size: 1.1rem; }

/* Section widgets bar */
.section-widgets {
  display: flex; gap: 0.5rem; padding: 0.75rem 0;
  flex-wrap: wrap;
}
.widget-link {
  display: inline-flex; align-items: center; gap: 0.4rem;
  padding: 0.4rem 0.9rem; background: rgba(122,162,247,0.08);
  border: 1px solid rgba(122,162,247,0.2); border-radius: 8px;
  font-size: 0.8rem; color: #7aa2f7; transition: all 0.2s;
}
.widget-link:hover {
  background: rgba(122,162,247,0.15); border-color: rgba(122,162,247,0.4);
}

/* Section content */
.section-body { padding: 1rem 0; }
.section-body h3 {
  font-size: 1.05rem; font-weight: 600; color: #e0e0e0;
  margin: 2rem 0 0.75rem; padding-bottom: 0.4rem;
  border-bottom: 1px solid #1a1a1a;
}
.section-body h3:first-child { margin-top: 0; }
.section-body h4 {
  font-size: 0.9rem; font-weight: 600; color: #bb9af7;
  margin: 1.2rem 0 0.5rem;
}
.section-body p { margin-bottom: 0.7rem; color: #aaa; }
.section-body strong { color: #e0e0e0; font-weight: 600; }
.section-body em { color: #777; }
.section-body ul, .section-body ol {
  padding-left: 1.4rem; margin-bottom: 0.7rem;
}
.section-body li { margin-bottom: 0.3rem; color: #aaa; }
.section-body li > ul { margin-top: 0.2rem; margin-bottom: 0; }
.section-body a { border-bottom: 1px solid rgba(122,162,247,0.3); }
.section-body a:hover { border-bottom-color: #7aa2f7; }
.section-body code {
  background: #141414; padding: 0.15rem 0.4rem; border-radius: 4px;
  font-family: 'JetBrains Mono', 'Fira Code', monospace; font-size: 0.85em;
  color: #ccc;
}
.section-body pre {
  background: #111; padding: 1rem; border-radius: 8px;
  overflow-x: auto; margin: 0.8rem 0; border: 1px solid #1a1a1a;
}
.section-body pre code { padding: 0; background: none; }
.section-body blockquote {
  border-left: 2px solid #333; padding-left: 1rem;
  color: #777; margin: 0.8rem 0;
}
.section-body hr {
  border: none; border-top: 1px solid #1a1a1a; margin: 1.5rem 0;
}
.section-body table {
  width: 100%; border-collapse: collapse; margin: 0.8rem 0;
}
.section-body th, .section-body td {
  padding: 0.5rem 0.75rem; border: 1px solid #1a1a1a; text-align: left;
}
.section-body th { background: #111; font-weight: 600; color: #ccc; }

/* ── Weather Detail (inside section view) ── */
.weather-detail { padding: 1rem 0; }
.weather-summary-line {
  font-size: 0.9rem; color: #888; text-align: center;
  margin-bottom: 1rem; font-weight: 400;
}
.weather-grid {
  display: flex; justify-content: center; gap: 0.5rem;
  overflow-x: auto; padding: 0.25rem 0;
}
.wx-card {
  text-align: center; min-width: 80px; padding: 0.75rem 0.5rem;
  background: #111; border-radius: 10px; flex-shrink: 0;
  border: 1px solid #1a1a1a; transition: border-color 0.2s;
}
.wx-card:hover { border-color: #333; }
.wx-time { font-size: 0.7rem; color: #555; font-weight: 600; text-transform: uppercase; }
.wx-icon { font-size: 1.5rem; margin: 0.15rem 0; }
.wx-temp { font-size: 1.05rem; font-weight: 700; }
.wx-wind { font-size: 0.65rem; color: #444; margin-top: 0.1rem; }
.wx-rain {
  text-align: center; color: #555; font-size: 0.8rem;
  margin-top: 0.75rem; font-style: italic;
}

/* 10-day forecast */
.forecast-section { margin-top: 2rem; }
.forecast-title {
  font-size: 0.7rem; font-weight: 600; letter-spacing: 2px;
  color: #444; margin-bottom: 1rem; text-transform: uppercase;
}
.forecast-grid {
  display: grid; grid-template-columns: repeat(5, 1fr); gap: 0.5rem;
}
.fc-card {
  text-align: center; padding: 0.75rem 0.4rem;
  background: #111; border-radius: 10px;
  border: 1px solid #1a1a1a; transition: border-color 0.2s;
}
.fc-card:hover { border-color: #333; }
.fc-day { font-size: 0.7rem; color: #888; font-weight: 600; }
.fc-date { font-size: 0.6rem; color: #444; }
.fc-icon { font-size: 1.4rem; margin: 0.3rem 0; }
.fc-hi { font-size: 0.95rem; font-weight: 700; }
.fc-lo { font-size: 0.75rem; color: #555; }
.fc-wind { font-size: 0.6rem; color: #444; margin-top: 0.2rem; }
.fc-precip { font-size: 0.6rem; color: #7aa2f7; }

/* Weather alerts */
.alerts-section { margin-top: 2rem; }
.alert-box {
  padding: 0.8rem 1rem; border-radius: 8px; margin-bottom: 0.5rem;
  border-left: 4px solid;
}
.alert-box.extreme, .alert-box.severe { border-color: #f7768e; background: rgba(247,118,142,0.06); }
.alert-box.moderate { border-color: #ff9e64; background: rgba(255,158,100,0.06); }
.alert-box.minor { border-color: #e0af68; background: rgba(224,175,104,0.06); }
.alert-box.unknown { border-color: #666; background: rgba(102,102,102,0.06); }
.alert-event { font-size: 0.85rem; font-weight: 600; color: #e0e0e0; }
.alert-headline { font-size: 0.8rem; color: #aaa; margin-top: 0.2rem; }
.alert-expires { font-size: 0.7rem; color: #555; margin-top: 0.2rem; }
.no-alerts { color: #333; font-size: 0.8rem; font-style: italic; padding: 0.5rem 0; }

/* ── Responsive ── */
@media (max-width: 900px) {
  .hero { padding: 0 6vw; }
  .card-grid { grid-template-columns: repeat(2, 1fr); }
  .forecast-grid { grid-template-columns: repeat(3, 1fr); }
  .topbar { padding: 0 1rem; }
  .weather-bar { display: none; }
  .dashboard-view { padding: 1.5rem 1rem 3rem; }
  .section-view { padding: 0 1rem 3rem; }
}
@media (max-width: 600px) {
  .card-grid { grid-template-columns: 1fr; }
  .forecast-grid { grid-template-columns: repeat(2, 1fr); }
  .greeting-name { font-size: 2.5rem; }
  .greeting-pre { font-size: 1.2rem; }
}
</style>
</head>
<body>

<!-- Hero (auto-fades after 2s) -->
<section class="hero" id="hero-section">
  <div class="hero-content">
    <p class="greeting-pre" id="greeting-text">Good morning,</p>
    <h1 class="greeting-name"><!--USER_NAME--></h1>
    <p class="hero-date"><!--DISPLAY_DATE--></p>
  </div>
</section>

<!-- Top Bar -->
<nav class="topbar" id="topbar">
  <span class="brand" onclick="showDashboard()"><!--APP_NAME--></span>
  <div class="weather-bar"><!--WEATHER_NAV--></div>
  <div class="nav-right">
    <!--PREV_LINK-->
    <a href="/archive" class="nav-btn">Archive</a>
    <!--EXTRA_NAV-->
    <!--NEXT_LINK-->
  </div>
</nav>

<!-- Dashboard View -->
<div class="dashboard-view" id="dashboard-view">
  <div class="dash-title">Today's Digest</div>
  <div class="card-grid">
    <!--CARDS-->
  </div>
</div>

<!-- Section View (hidden, populated by JS) -->
<div class="section-view" id="section-view">
  <div class="section-toolbar">
    <button class="back-btn" onclick="showDashboard()">← Dashboard</button>
    <div class="section-nav-bar">
      <button class="sec-nav-btn" id="prev-sec-btn" onclick="navSection(-1)">◀</button>
      <div class="section-nav-title">
        <span class="section-nav-icon" id="sec-nav-icon"></span>
        <span id="sec-nav-name"></span>
      </div>
      <button class="sec-nav-btn" id="next-sec-btn" onclick="navSection(1)">▶</button>
    </div>
  </div>
  <div class="section-widgets" id="section-widgets"></div>
  <div class="section-body" id="section-body"></div>
</div>

<!-- Hidden section data (populated by server) -->
<div id="section-data" style="display:none">
  <!--SECTIONS-->
</div>

<script>
// Section metadata
var SECTIONS = <!--SECTIONS_JSON-->;
var currentSection = -1;

// Hero auto-fade
(function() {
  var h = new Date().getHours();
  var g = h < 12 ? 'Good morning,' : h < 17 ? 'Good afternoon,' : 'Good evening,';
  document.getElementById('greeting-text').textContent = g;
  var hero = document.getElementById('hero-section');
  setTimeout(function() {
    hero.classList.add('fade-out');
    setTimeout(function() {
      hero.classList.add('collapsed');
    }, 600);
  }, 2000);
})();

function showDashboard() {
  document.getElementById('dashboard-view').style.display = '';
  document.getElementById('section-view').classList.remove('active');
  window.location.hash = '';
  currentSection = -1;
}

function showSection(idx) {
  if (idx < 0 || idx >= SECTIONS.length) return;
  currentSection = idx;
  var sec = SECTIONS[idx];

  document.getElementById('dashboard-view').style.display = 'none';
  var sv = document.getElementById('section-view');
  sv.classList.remove('active');
  void sv.offsetWidth; // trigger reflow for animation
  sv.classList.add('active');

  document.getElementById('sec-nav-icon').textContent = sec.icon;
  document.getElementById('sec-nav-name').textContent = sec.header;

  // Nav buttons
  document.getElementById('prev-sec-btn').className =
    'sec-nav-btn' + (idx === 0 ? ' disabled' : '');
  document.getElementById('next-sec-btn').className =
    'sec-nav-btn' + (idx === SECTIONS.length - 1 ? ' disabled' : '');

  // Widgets
  var whtml = '';
  var navLinks = JSON.parse('<!--NAV_LINKS_JSON-->');
  if (sec.type === 'career' && navLinks.length > 0) {
    navLinks.forEach(function(link) {
      whtml += '<a href="' + link.url + '" class="widget-link" target="_blank">' + link.label + ' →</a>';
    });
  }
  document.getElementById('section-widgets').innerHTML = whtml;

  // Content
  var contentEl = document.getElementById('sec-content-' + idx);
  var body = contentEl ? contentEl.innerHTML : '';

  // For weather section, append 10-day forecast + alerts
  if (sec.type === 'weather') {
    body += '<div id="forecast-container"></div><div id="alerts-container"></div>';
  }

  document.getElementById('section-body').innerHTML = body;
  window.location.hash = sec.type;

  // Fetch weather data if weather section
  if (sec.type === 'weather') {
    fetchWeather();
  }

  // Scroll to top of section
  document.getElementById('topbar').scrollIntoView({behavior: 'instant'});
}

function navSection(delta) {
  var next = currentSection + delta;
  if (next >= 0 && next < SECTIONS.length) showSection(next);
}

// Temperature color scale
function tempColor(t) {
  if (t < 32) return '#7aa2f7';
  if (t < 45) return '#7dcfff';
  if (t < 60) return '#9ece6a';
  if (t < 75) return '#e0af68';
  if (t < 90) return '#ff9e64';
  return '#f7768e';
}

function fetchWeather() {
  var fc = document.getElementById('forecast-container');
  var ac = document.getElementById('alerts-container');
  if (!fc) return;

  fc.innerHTML = '<div class="forecast-section"><div class="forecast-title">Loading 10-day forecast...</div></div>';

  fetch('/api/weather')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      // 10-day forecast
      if (data.forecast && data.forecast.daily) {
        var d = data.forecast.daily;
        var html = '<div class="forecast-section"><div class="forecast-title">10-Day Forecast</div><div class="forecast-grid">';
        for (var i = 0; i < d.time.length; i++) {
          var dt = new Date(d.time[i] + 'T12:00:00');
          var dayName = i === 0 ? 'Today' : dt.toLocaleDateString('en-US', {weekday: 'short'});
          var dateStr = dt.toLocaleDateString('en-US', {month: 'short', day: 'numeric'});
          var hi = Math.round(d.temperature_2m_max[i]);
          var lo = Math.round(d.temperature_2m_min[i]);
          var code = d.weathercode[i];
          var icon = data.wmo_icons[code] || '🌡️';
          var wind = d.windspeed_10m_max ? Math.round(d.windspeed_10m_max[i]) + ' mph' : '';
          var precip = d.precipitation_sum ? d.precipitation_sum[i] : 0;
          html += '<div class="fc-card">';
          html += '<div class="fc-day">' + dayName + '</div>';
          html += '<div class="fc-date">' + dateStr + '</div>';
          html += '<div class="fc-icon">' + icon + '</div>';
          html += '<div class="fc-hi" style="color:' + tempColor(hi) + '">' + hi + '°</div>';
          html += '<div class="fc-lo">' + lo + '°</div>';
          if (wind) html += '<div class="fc-wind">💨 ' + wind + '</div>';
          if (precip > 0) html += '<div class="fc-precip">💧 ' + precip.toFixed(1) + ' in</div>';
          html += '</div>';
        }
        html += '</div></div>';
        fc.innerHTML = html;
      } else {
        fc.innerHTML = '<div class="forecast-section"><div class="no-alerts">Forecast unavailable</div></div>';
      }

      // Alerts
      if (data.alerts && data.alerts.length > 0) {
        var ahtml = '<div class="alerts-section"><div class="forecast-title">⚠️ Active Weather Alerts</div>';
        data.alerts.forEach(function(a) {
          var sev = (a.severity || 'unknown').toLowerCase();
          ahtml += '<div class="alert-box ' + sev + '">';
          ahtml += '<div class="alert-event">' + a.event + '</div>';
          if (a.headline) ahtml += '<div class="alert-headline">' + a.headline + '</div>';
          if (a.expires) {
            var exp = new Date(a.expires);
            ahtml += '<div class="alert-expires">Expires: ' + exp.toLocaleString() + '</div>';
          }
          ahtml += '</div>';
        });
        ahtml += '</div>';
        ac.innerHTML = ahtml;
      } else {
        ac.innerHTML = '<div class="alerts-section"><div class="no-alerts">No active weather alerts ✓</div></div>';
      }
    })
    .catch(function() {
      fc.innerHTML = '<div class="forecast-section"><div class="no-alerts">Weather data unavailable</div></div>';
    });
}

// Hash routing
function handleHash() {
  var hash = window.location.hash.slice(1);
  if (!hash) { showDashboard(); return; }
  for (var i = 0; i < SECTIONS.length; i++) {
    if (SECTIONS[i].type === hash) { showSection(i); return; }
  }
  showDashboard();
}
window.addEventListener('hashchange', handleHash);
if (window.location.hash) {
  // Defer to after hero fade
  setTimeout(handleHash, 100);
}

// Keyboard shortcuts
document.addEventListener('keydown', function(e) {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (e.key === 'Escape') { showDashboard(); return; }
  if (e.key === 'ArrowLeft') {
    if (currentSection >= 0) navSection(-1);
    else {
      var el = document.querySelector('.nav-arrow[href]:not(.disabled)');
      if (el) window.location = el.href;
    }
    return;
  }
  if (e.key === 'ArrowRight') {
    if (currentSection >= 0) navSection(1);
    else {
      var els = document.querySelectorAll('.nav-arrow[href]:not(.disabled)');
      if (els.length > 1) window.location = els[1].href;
    }
    return;
  }
  var n = parseInt(e.key);
  if (n >= 1 && n <= 9 && n <= SECTIONS.length) {
    showSection(n - 1);
  }
});

// Hero scroll fade
window.addEventListener('scroll', function() {
  var hero = document.querySelector('.hero');
  if (!hero) return;
  var r = Math.min(window.scrollY / (window.innerHeight * 0.7), 1);
  hero.style.opacity = 1 - r;
  hero.style.transform = 'translateY(' + (r * -40) + 'px)';
}, { passive: true });
</script>
</body>
</html>"""

ARCHIVE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title><!--APP_NAME--> — Archive</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🐾</text></svg>">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: #0a0a0a; color: #e0e0e0;
  font-family: 'Inter', sans-serif; line-height: 1.6;
}
a { color: #7aa2f7; text-decoration: none; }
a:hover { color: #9bb8fa; }
header {
  background: rgba(10,10,10,0.92); backdrop-filter: blur(12px);
  border-bottom: 1px solid #1a1a1a; padding: 0 2rem; height: 56px;
  display: flex; justify-content: space-between; align-items: center;
  position: sticky; top: 0; z-index: 10;
}
.brand { font-size: 0.85rem; font-weight: 600; letter-spacing: 0.5px; }
.brand-dim { color: #555; font-weight: 400; }
.archive-container { max-width: 720px; margin: 3rem auto; padding: 0 1.5rem; }
.archive-container > h2 {
  font-size: clamp(2rem, 4vw, 3rem); font-weight: 700;
  color: #f0f0f0; margin-bottom: 0.5rem; letter-spacing: -0.02em;
}
.archive-date-line {
  color: #555; margin-bottom: 2rem; font-size: 0.95rem;
}
.cal-month { margin-bottom: 2.5rem; }
.cal-month-label {
  font-size: 0.7rem; font-weight: 600; letter-spacing: 2px;
  color: #444; margin-bottom: 0.75rem; text-transform: uppercase;
}
.cal-grid {
  display: grid; grid-template-columns: repeat(7, 1fr); gap: 4px;
}
.cal-dow {
  text-align: center; font-size: 0.65rem; font-weight: 600;
  color: #333; padding: 0.3rem 0; letter-spacing: 1px;
}
.cal-day {
  aspect-ratio: 1; display: flex; align-items: center; justify-content: center;
  border-radius: 6px; font-size: 0.8rem; font-weight: 500;
  transition: all 0.2s; position: relative;
}
.cal-day.empty { background: transparent; }
.cal-day.no-digest { background: #111; color: #333; cursor: default; }
.cal-day.has-digest {
  background: #b8860b; color: #0a0a0a; cursor: pointer; font-weight: 700;
}
.cal-day.has-digest:hover {
  background: #daa520; transform: scale(1.08);
  box-shadow: 0 0 12px rgba(218, 165, 32, 0.3);
}
.cal-day.today { outline: 2px solid #7aa2f7; outline-offset: -1px; }
.empty-msg { color: #555; text-align: center; padding: 4rem; }
</style>
</head>
<body>
<header>
  <span class="brand"><!--APP_NAME--></span>
  <a href="/latest">Latest</a>
</header>
<div class="archive-container">
  <h2><!--APP_NAME--></h2>
  <p class="archive-date-line"><!--ARCHIVE_DATE--></p>
  <!--ITEMS-->
</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def classify_section(header: str) -> str:
    h = header.lower()
    if "weather" in h:
        return "weather"
    if "career" in h:
        return "career"
    if "boxing" in h:
        return "boxing"
    if "tech" in h:
        return "technology"
    if "news" in h:
        return "news"
    if "calendar" in h:
        return "calendar"
    if "misc" in h:
        return "miscellaneous"
    return "other"


def get_digest_dates() -> list[str]:
    dates = []
    for f in DIGEST_DIR.glob("????-??-??.md"):
        if f.stem not in ("digest-config",):
            dates.append(f.stem)
    return sorted(dates)


def get_adjacent_dates(date_str: str, all_dates: list[str]):
    if date_str not in all_dates:
        return None, None
    idx = all_dates.index(date_str)
    prev_d = all_dates[idx - 1] if idx > 0 else None
    next_d = all_dates[idx + 1] if idx < len(all_dates) - 1 else None
    return prev_d, next_d


def temp_color(temp_str: str) -> str:
    try:
        t = int(re.sub(r"[^0-9\-]", "", temp_str))
    except (ValueError, TypeError):
        return "#999"
    if t < 32:
        return "#7aa2f7"
    if t < 45:
        return "#7dcfff"
    if t < 60:
        return "#9ece6a"
    if t < 75:
        return "#e0af68"
    if t < 90:
        return "#ff9e64"
    return "#f7768e"


def strip_html(html: str) -> str:
    """Strip HTML tags and return plain text, truncated."""
    text = re.sub(r"<[^>]+>", "", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:120] + "..." if len(text) > 120 else text


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_weather_block(code_text: str) -> dict | None:
    lines = [l for l in code_text.strip().split("\n") if l.strip()]
    if len(lines) < 5:
        return None
    times = lines[0].split()
    icons = lines[1].split()
    temps = lines[2].split()
    wind_dirs = lines[3].split()
    wind_speeds = lines[4].split()
    rain_note = lines[5].strip() if len(lines) > 5 else ""
    n = min(len(times), len(icons), len(temps))
    hours = []
    for i in range(n):
        hours.append(
            {
                "time": times[i],
                "icon": icons[i],
                "temp": temps[i],
                "wind_dir": wind_dirs[i] if i < len(wind_dirs) else "",
                "wind_speed": wind_speeds[i] if i < len(wind_speeds) else "",
            }
        )
    return {"hours": hours, "rain_note": rain_note}


def parse_digest(content: str) -> tuple[str, list[dict]]:
    title_match = re.match(r"^# (.+)$", content, re.MULTILINE)
    title = title_match.group(1) if title_match else "Daily Digest"
    sections = []
    parts = re.split(r"^## ", content, flags=re.MULTILINE)
    for part in parts[1:]:
        lines = part.split("\n", 1)
        header = lines[0].strip()
        body = lines[1].strip() if len(lines) > 1 else ""
        sections.append({"header": header, "body": body})
    return title, sections


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_weather_nav(body: str) -> str:
    code_match = re.search(r"```\n?(.*?)\n?```", body, re.DOTALL)
    if not code_match:
        return ""
    weather = parse_weather_block(code_match.group(1))
    if not weather:
        return ""
    chips = []
    for h in weather["hours"]:
        color = temp_color(h["temp"])
        temp = h["temp"].replace("F", "\u00b0")
        title = f'{h["time"]} \u2014 {h["wind_dir"]} {h["wind_speed"]}'
        chips.append(
            f'<span class="weather-chip" title="{title}">'
            f'<span class="chip-icon">{h["icon"]}</span>'
            f'<span class="chip-temp" style="color:{color}">{temp}</span>'
            f'<span class="chip-time">{h["time"]}</span>'
            f"</span>"
        )
    return "".join(chips)


def render_weather_content(body: str) -> str:
    summary_match = re.search(r"\*\*(.+?)\*\*", body)
    summary = summary_match.group(1) if summary_match else ""
    code_match = re.search(r"```\n?(.*?)\n?```", body, re.DOTALL)
    if not code_match:
        return f'<div class="weather-summary-line">{summary}</div>'
    weather = parse_weather_block(code_match.group(1))
    if not weather:
        return f'<div class="weather-summary-line">{summary}</div>'
    cards = []
    for h in weather["hours"]:
        color = temp_color(h["temp"])
        cards.append(
            f'<div class="wx-card">'
            f'<div class="wx-time">{h["time"]}</div>'
            f'<div class="wx-icon">{h["icon"]}</div>'
            f'<div class="wx-temp" style="color:{color}">{h["temp"]}</div>'
            f'<div class="wx-wind">{h["wind_dir"]} {h["wind_speed"]}</div>'
            f"</div>"
        )
    rain = (
        f'<div class="wx-rain">{weather["rain_note"]}</div>'
        if weather["rain_note"]
        else ""
    )
    return (
        f'<div class="weather-detail">'
        f'<div class="weather-summary-line">{summary}</div>'
        f'<div class="weather-grid">{"".join(cards)}</div>'
        f"{rain}</div>"
    )


def render_weather_card_preview() -> str:
    """Mini 10-day preview for the dashboard card."""
    data = fetch_weather_forecast()
    if not data or "daily" not in data:
        return '<div class="card-preview">Weather data loading...</div>'
    d = data["daily"]
    html = '<div class="weather-card-grid">'
    for i in range(min(7, len(d["time"]))):
        dt = datetime.datetime.strptime(d["time"][i], "%Y-%m-%d")
        day_name = "Today" if i == 0 else dt.strftime("%a")
        hi = round(d["temperature_2m_max"][i])
        lo = round(d["temperature_2m_min"][i])
        code = d["weathercode"][i]
        icon = WMO_ICONS.get(code, "\U0001f321\ufe0f")
        color = temp_color(str(hi))
        html += (
            f'<div class="wx-mini">'
            f'<div class="wx-mini-day">{day_name}</div>'
            f'<div class="wx-mini-icon">{icon}</div>'
            f'<div class="wx-mini-temp" style="color:{color}">{hi}\u00b0</div>'
            f'<div class="wx-mini-lo">{lo}\u00b0</div>'
            f"</div>"
        )
    html += "</div>"
    return html


def render_digest(date_str: str) -> str | None:
    md_file = DIGEST_DIR / f"{date_str}.md"
    if not md_file.exists():
        return None
    content = md_file.read_text()
    title, sections = parse_digest(content)
    md_renderer = markdown.Markdown(
        extensions=["tables", "fenced_code", "sane_lists", "smarty"]
    )
    all_dates = get_digest_dates()
    prev_date, next_date = get_adjacent_dates(date_str, all_dates)

    # Classify sections
    weather_body = None
    content_sections = []
    for s in sections:
        stype = classify_section(s["header"])
        if stype == "weather":
            weather_body = s["body"]
        content_sections.append({**s, "type": stype})

    # Weather nav bar
    weather_nav_html = render_weather_nav(weather_body) if weather_body else ""

    # Build cards HTML and hidden section content
    cards_html = ""
    sections_html = ""
    sections_json = []

    for idx, sec in enumerate(content_sections):
        stype = sec["type"]
        icon = SECTION_ICONS.get(stype, SECTION_ICONS["other"])
        accent = SECTION_ACCENTS.get(stype, "#666")

        # Render full content
        if stype == "weather" and weather_body:
            body_html = render_weather_content(weather_body)
        else:
            body_html = md_renderer.convert(sec["body"])
            md_renderer.reset()

        # Card preview
        preview = strip_html(body_html)
        is_weather = stype == "weather"

        cards_html += (
            f'<div class="dash-card{"  weather-card" if is_weather else ""}" '
            f'style="--card-accent:{accent}" onclick="showSection({idx})">'
            f'<div class="card-header">'
            f'<span class="card-icon">{icon}</span>'
            f'<span class="card-name">{sec["header"]}</span>'
            f"</div>"
        )
        if is_weather:
            cards_html += render_weather_card_preview()
        else:
            cards_html += f'<div class="card-preview">{preview}</div>'

        # Widget hints on cards
        if stype == "career":
            cards_html += '<div class="card-widget">\U0001f517 Company Research</div>'

        cards_html += "</div>"

        # Hidden section content
        sections_html += f'<div id="sec-content-{idx}">{body_html}</div>'

        sections_json.append({
            "type": stype,
            "header": sec["header"],
            "icon": icon,
            "accent": accent,
        })

    # Navigation links
    prev_link = (
        f'<a href="/{prev_date}" class="nav-arrow" title="{prev_date}">\u25c0</a>'
        if prev_date
        else '<span class="nav-arrow disabled">\u25c0</span>'
    )
    next_link = (
        f'<a href="/{next_date}" class="nav-arrow" title="{next_date}">\u25b6</a>'
        if next_date
        else '<span class="nav-arrow disabled">\u25b6</span>'
    )

    # Display date
    try:
        dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        display_date = dt.strftime("%A, %B %d, %Y")
    except ValueError:
        display_date = date_str

    extra_nav = "".join(
        f'<a href="{link["url"]}" class="nav-btn">{link["label"]}</a>'
        for link in NAV_LINKS
    )
    user_display = f"{USER_NAME}." if USER_NAME else ""

    return (
        PAGE_TEMPLATE.replace("<!--TITLE-->", title)
        .replace("<!--DISPLAY_DATE-->", display_date)
        .replace("<!--WEATHER_NAV-->", weather_nav_html)
        .replace("<!--PREV_LINK-->", prev_link)
        .replace("<!--NEXT_LINK-->", next_link)
        .replace("<!--CARDS-->", cards_html)
        .replace("<!--SECTIONS-->", sections_html)
        .replace("<!--SECTIONS_JSON-->", json.dumps(sections_json))
        .replace("<!--USER_NAME-->", user_display)
        .replace("<!--APP_NAME-->", APP_NAME)
        .replace("<!--EXTRA_NAV-->", extra_nav)
        .replace("<!--NAV_LINKS_JSON-->", json.dumps(NAV_LINKS).replace("'", "\\'"))
    )


def render_archive() -> str:
    dates = get_digest_dates()
    date_set = set(dates)
    today_str = datetime.date.today().isoformat()

    if not dates:
        return (
            ARCHIVE_TEMPLATE.replace("<!--ITEMS-->", '<p class="empty-msg">No digests yet.</p>')
            .replace("<!--ARCHIVE_DATE-->", "")
            .replace("<!--APP_NAME-->", APP_NAME)
        )

    first = datetime.datetime.strptime(dates[0], "%Y-%m-%d").date()
    last = datetime.date.today()
    months = []
    d = last.replace(day=1)
    while d >= first.replace(day=1):
        months.append((d.year, d.month))
        if d.month == 1:
            d = d.replace(year=d.year - 1, month=12)
        else:
            d = d.replace(month=d.month - 1)

    dow_headers = "".join(
        f'<div class="cal-dow">{name}</div>'
        for name in ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    )

    items_html = ""
    for year, month in months:
        try:
            label = datetime.date(year, month, 1).strftime("%B %Y")
        except ValueError:
            label = f"{year}-{month:02d}"

        first_weekday = datetime.date(year, month, 1).weekday()
        start_col = (first_weekday + 1) % 7
        days_in_month = cal_mod.monthrange(year, month)[1]

        items_html += f'<div class="cal-month"><div class="cal-month-label">{label}</div>'
        items_html += f'<div class="cal-grid">{dow_headers}'

        for _ in range(start_col):
            items_html += '<div class="cal-day empty"></div>'

        for day in range(1, days_in_month + 1):
            date_str_d = f"{year}-{month:02d}-{day:02d}"
            has = date_str_d in date_set
            today_cls = " today" if date_str_d == today_str else ""
            if has:
                items_html += (
                    f'<a href="/{date_str_d}" class="cal-day has-digest{today_cls}" '
                    f'title="{date_str_d}">{day}</a>'
                )
            else:
                items_html += (
                    f'<div class="cal-day no-digest{today_cls}">{day}</div>'
                )

        items_html += "</div></div>"

    today_label = datetime.date.today().strftime("%A, %B %d, %Y")
    return (
        ARCHIVE_TEMPLATE.replace("<!--ARCHIVE_DATE-->", today_label)
        .replace("<!--ITEMS-->", items_html)
        .replace("<!--APP_NAME-->", APP_NAME)
    )


def render_error(code: int, message: str) -> str:
    return (
        f"<!DOCTYPE html><html><head>"
        f"<link href='https://fonts.googleapis.com/css2?family=Inter:wght@300;700&display=swap' rel='stylesheet'>"
        f"<style>body{{background:#0a0a0a;color:#e0e0e0;font-family:'Inter',sans-serif;"
        f"display:flex;justify-content:center;align-items:center;min-height:100vh;}}"
        f"a{{color:#7aa2f7;}}</style></head><body>"
        f'<div style="text-align:center">'
        f'<h1 style="font-size:5rem;font-weight:700;color:#1a1a1a">{code}</h1>'
        f'<p style="color:#555">{message}</p>'
        f'<p style="margin-top:1.5rem"><a href="/latest">Latest digest</a>'
        f" &middot; <a href=\"/archive\">Archive</a></p>"
        f"</div></body></html>"
    )


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------

class DigestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path.strip("/")

        if path in ("", "latest"):
            dates = get_digest_dates()
            if dates:
                self._redirect(f"/{dates[-1]}")
            else:
                self._html(200, render_error(404, "No digests available yet."))

        elif path == "archive":
            self._html(200, render_archive())

        elif path == "api/weather":
            self._json(200, {
                "forecast": fetch_weather_forecast(),
                "alerts": fetch_weather_alerts(),
                "wmo_icons": {str(k): v for k, v in WMO_ICONS.items()},
            })

        elif path == "health":
            self._text(200, "ok")

        elif re.match(r"\d{4}-\d{2}-\d{2}$", path):
            html = render_digest(path)
            if html:
                self._html(200, html)
            else:
                self._html(404, render_error(404, f"No digest found for {path}"))

        else:
            self._html(404, render_error(404, "Page not found"))

    def _html(self, code: int, body: str):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body.encode())

    def _json(self, code: int, data: dict):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "max-age=300")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _text(self, code: int, body: str):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(body.encode())

    def _redirect(self, location: str):
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def log_message(self, fmt, *args):
        pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if DIGEST_DIR is None:
        print("ERROR: DIGEST_DIR environment variable is required.", file=sys.stderr)
        print("Set it to the directory containing your YYYY-MM-DD.md digest files.", file=sys.stderr)
        sys.exit(1)
    if not DIGEST_DIR.is_dir():
        print(f"ERROR: DIGEST_DIR '{DIGEST_DIR}' is not a directory.", file=sys.stderr)
        sys.exit(1)
    server = HTTPServer((HOST, PORT), DigestHandler)
    server.socket.setsockopt(
        __import__("socket").SOL_SOCKET,
        __import__("socket").SO_REUSEADDR,
        1,
    )
    print(f"{APP_NAME} server running on http://{HOST}:{PORT}")
    print(f"Serving digests from {DIGEST_DIR}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
