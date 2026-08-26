# No More Meme v2

A small web app that searches [Brave Image Search](https://api-dashboard.search.brave.com/api-reference/images/image_search) for a fitting photo, then makes a classic meme with `NO MORE` on top and your text on the bottom.

## Setup

1. Create a Brave Search API key and set `BRAVE_SEARCH_API_KEY` in your environment (see `.env.example`).
2. Create a local environment and install dependencies: `uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt`.
3. Start the app: `.venv/bin/python app.py`.

Open `http://localhost:8000`, enter a word or short phrase, and download the result.

## Shareable URLs

The path controls the bottom meme text. Spaces can be written as underscores:

`http://localhost:8000/no_more_meetings`

The optional `q` parameter controls the Brave image search independently of the displayed text:

`http://localhost:8000/no_more_meetings?q=tired_office_worker`

Brave results, downloaded source images, and rendered memes are cached locally for 12 hours in `api_cache.sqlite`.
