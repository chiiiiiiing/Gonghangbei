"""Flask service for the AlphaLens bank-rates submission."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.rates.engine import (  # noqa: E402
    analyze_document,
    append_review,
    load_backtest,
    load_forecast,
    load_status,
)
from src.rates.schema import DISCLAIMER  # noqa: E402


app = Flask(__name__, static_folder=None)


@app.after_request
def disable_api_caching(response):
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/")
def index():
    return send_from_directory(APP_DIR, "index.html")


@app.get("/assets/<path:filename>")
def assets(filename: str):
    return send_from_directory(APP_DIR / "assets", filename)


@app.get("/api/rates/status")
def rates_status():
    return jsonify(load_status())


@app.get("/api/rates/forecast")
def rates_forecast():
    as_of = request.args.get("as_of") or None
    try:
        horizon = int(request.args.get("horizon", "5"))
        result = load_forecast(as_of=as_of, horizon=horizon)
    except ValueError as exc:
        return jsonify({"error": str(exc), "disclaimer": DISCLAIMER}), 400
    return jsonify(result)


@app.get("/api/rates/backtest")
def rates_backtest():
    return jsonify(load_backtest())


@app.post("/api/rates/analyze")
def rates_analyze():
    try:
        result = analyze_document(request.get_json(silent=True) or {})
    except ValueError as exc:
        return jsonify({"error": str(exc), "disclaimer": DISCLAIMER}), 400
    return jsonify(result)


@app.post("/api/rates/review")
def rates_review():
    try:
        result = append_review(request.get_json(silent=True) or {})
    except ValueError as exc:
        return jsonify({"error": str(exc), "disclaimer": DISCLAIMER}), 400
    return jsonify({"review": result, "saved": True, "disclaimer": DISCLAIMER}), 201


@app.errorhandler(404)
def not_found(_error):
    if request.path.startswith("/api/"):
        return jsonify({"error": "接口不存在", "disclaimer": DISCLAIMER}), 404
    return send_from_directory(APP_DIR, "index.html")


if __name__ == "__main__":
    host = os.getenv("ALPHALENS_HOST", "127.0.0.1")
    port = int(os.getenv("ALPHALENS_PORT", "8701"))
    app.run(host=host, port=port, debug=False)
