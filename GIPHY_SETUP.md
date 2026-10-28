# Top rail celebration GIFs

Each new goal or 10-rail milestone requests a random Terminator GIF from GIPHY.
The dashboard uses the existing Terminator GIFs if GIPHY is unavailable.

## Server configuration

Set `GIPHY_API_KEY` in the Flask/Gunicorn service environment, then restart the
service. Alternatively, create `giphy_settings.py` beside `flask_app.py`:

```python
GIPHY_API_KEY = 'YOUR_GIPHY_API_KEY'
```

The environment variable takes precedence. The settings file is ignored by Git
and must be configured separately on the production server; pushing code does
not transfer it. A local settings file has already been configured.

GIPHY requires browser-side API requests, so the key is available to dashboard
browsers through the uncached celebration configuration endpoint. It is never
embedded in the dashboard HTML or committed to Git. The browser contacts GIPHY
only when a celebration starts, rather than on each dashboard refresh.

Requests use the `terminator` tag and the `pg-13` rating limit. A repeated GIPHY
ID is retried once. The previous ID is remembered without storing GIPHY media
URLs. If an API request or image load fails, a fallback GIF is displayed.

GIPHY API documentation: https://developers.giphy.com/docs/api/endpoint/
