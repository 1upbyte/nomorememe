"""Brave Image Search-powered No More meme generator."""
from __future__ import annotations

import io
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote

import requests
from dotenv import load_dotenv
from flask import Flask, render_template, request, send_file
from PIL import Image, ImageDraw, ImageFont, ImageOps


BRAVE_IMAGES_URL = "https://api.search.brave.com/res/v1/images/search"
CANVAS_SIZE = (1200, 1200)
MAX_BOTTOM_TEXT = 80
CACHE_TTL_SECONDS = 12 * 60 * 60
CACHE_PATH = Path(__file__).with_name("api_cache.sqlite")
ERROR_IMAGE_PATH = Path(__file__).with_name("error.png")
load_dotenv()
app = Flask(__name__)


def brave_search_key() -> str:
    return os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()


def normalize_text(value: str) -> str:
    return " ".join(value.split())


def normalize_url_text(value: str) -> str:
    """Keep original generator URLs readable: `no_more` becomes `no more`."""
    return normalize_text(value.replace("_", " "))


def cache_key(namespace: str, value: str) -> str:
    return hashlib.sha256(f"{namespace}:{value}".encode()).hexdigest()


def cache_get(namespace: str, value: str) -> bytes | None:
    """Return a non-expired cache entry without letting cache errors block a meme."""
    try:
        now = time.time()
        with sqlite3.connect(CACHE_PATH, timeout=5) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value BLOB NOT NULL, expires_at REAL NOT NULL)")
            key = cache_key(namespace, value)
            connection.execute("DELETE FROM cache WHERE key = ? AND expires_at <= ?", (key, now))
            row = connection.execute("SELECT value FROM cache WHERE key = ?", (key,)).fetchone()
            return row[0] if row else None
    except sqlite3.Error:
        return None


def cache_put(namespace: str, value: str, data: bytes) -> None:
    try:
        with sqlite3.connect(CACHE_PATH, timeout=5) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value BLOB NOT NULL, expires_at REAL NOT NULL)")
            connection.execute(
                "INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
                (cache_key(namespace, value), data, time.time() + CACHE_TTL_SECONDS),
            )
    except sqlite3.Error:
        pass


def search_brave_images(query: str) -> list[str]:
    if not (key := brave_search_key()):
        raise RuntimeError("BRAVE_SEARCH_API_KEY has not been configured on the server.")
    if cached_result := cache_get("brave-results", query):
        return json.loads(cached_result)
    response = requests.get(
        BRAVE_IMAGES_URL,
        params={"q": query, "count": 10, "country": "US", "search_lang": "en", "safesearch": "strict"},
        headers={"Accept": "application/json", "X-Subscription-Token": key},
        timeout=12,
    )
    response.raise_for_status()
    image_urls = []
    for result in response.json().get("results", []):
        image_url = result.get("properties", {}).get("url") or result.get("thumbnail")
        if isinstance(image_url, str) and image_url.startswith(("https://", "http://")):
            image_urls.append(image_url)
    cache_put("brave-results", query, json.dumps(image_urls).encode())
    return image_urls


def meme_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Impact.ttf",
        "/Library/Fonts/Impact.ttf",
    ):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def draw_fitted_line(draw: ImageDraw.ImageDraw, text: str, *, y: int, max_size: int, bottom: bool = False) -> None:
    """Draw one meme line, reducing its type size until it fits inside the canvas."""
    stroke = 4
    for size in range(max_size, 17, -2):
        current_font = meme_font(size)
        bounds = draw.textbbox((0, 0), text, font=current_font, stroke_width=stroke)
        if bounds[2] - bounds[0] <= CANVAS_SIZE[0] - 72:
            break
    else:
        current_font = meme_font(16)
        bounds = draw.textbbox((0, 0), text, font=current_font, stroke_width=stroke)

    text_width, text_height = bounds[2] - bounds[0], bounds[3] - bounds[1]
    x = (CANVAS_SIZE[0] - text_width) / 2
    text_y = CANVAS_SIZE[1] - text_height - 45 - bounds[1] if bottom else y - bounds[1]
    draw.text((x, text_y), text, font=current_font, fill="white", stroke_width=stroke, stroke_fill="black")


def render_meme(image_url: str, bottom_text: str) -> io.BytesIO:
    photo_bytes = cache_get("source-image", image_url)
    if photo_bytes is None:
        photo = requests.get(image_url, timeout=15)
        photo.raise_for_status()
        photo_bytes = photo.content
        cache_put("source-image", image_url, photo_bytes)
    image = ImageOps.fit(
        Image.open(io.BytesIO(photo_bytes)).convert("RGB"),
        CANVAS_SIZE,
        method=Image.Resampling.LANCZOS,
    )
    draw = ImageDraw.Draw(image)
    draw_fitted_line(draw, "NO MORE", y=42, max_size=132)
    draw_fitted_line(draw, bottom_text.upper(), y=0, max_size=132, bottom=True)
    output = io.BytesIO()
    image.save(output, "JPEG", quality=92, optimize=True)
    output.seek(0)
    return output


def render_first_available(image_urls: list[str], bottom_text: str) -> io.BytesIO:
    for image_url in image_urls:
        try:
            return render_meme(image_url, bottom_text)
        except (requests.RequestException, OSError):
            continue
    raise OSError("No Brave image result could be downloaded.")


def meme_response(meme_bytes: bytes, bottom_text: str):
    response = send_file(io.BytesIO(meme_bytes), mimetype="image/jpeg", download_name=f"no-more-{quote(bottom_text[:30])}.jpg")
    response.headers["Cache-Control"] = f"private, max-age={CACHE_TTL_SECONDS}"
    return response


def error_response():
    """Always return the familiar error image so direct meme URLs remain image-safe."""
    response = send_file(ERROR_IMAGE_PATH, mimetype="image/png")
    response.headers["Cache-Control"] = "no-store"
    return response


def generate_response(bottom_text: str, image_query: str):
    if not bottom_text:
        return error_response()
    if len(bottom_text) > MAX_BOTTOM_TEXT:
        return error_response()
    if not image_query:
        image_query = bottom_text
    if len(image_query) > 400 or len(image_query.split()) > 50:
        return error_response()
    result_cache_value = f"{bottom_text}\n{image_query}"
    if cached_meme := cache_get("meme", result_cache_value):
        return meme_response(cached_meme, bottom_text)
    try:
        image_urls = search_brave_images(image_query)
        if not image_urls:
            return error_response()
        output = render_first_available(image_urls, bottom_text)
    except RuntimeError as error:
        return error_response()
    except (requests.RequestException, OSError):
        return error_response()
    meme_bytes = output.getvalue()
    cache_put("meme", result_cache_value, meme_bytes)
    return meme_response(meme_bytes, bottom_text)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/generate")
def generate():
    bottom_text = normalize_text(request.args.get("text", ""))
    image_query = normalize_url_text(request.args.get("q", ""))
    return generate_response(bottom_text, image_query)


@app.get("/<path:meme_text>")
def generate_from_url(meme_text: str):
    """Generate an image directly from a shareable URL, as in the original app."""
    bottom_text = normalize_url_text(meme_text)
    image_query = normalize_url_text(request.args.get("q", ""))
    return generate_response(bottom_text, image_query)


@app.errorhandler(Exception)
def unexpected_error(_error):
    return error_response()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), debug=True)
