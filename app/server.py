"""Flask service for the AlphaLens bank-rates submission."""

from __future__ import annotations

import hashlib
import io
import os
import sys
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.rates.engine import (  # noqa: E402
    analyze_document,
    append_review,
    load_backtest,
    load_demo_cases,
    load_evidence,
    load_forecast,
    load_report,
    load_reviews,
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


@app.get("/vendor/<path:filename>")
def vendor(filename: str):
    return send_from_directory(APP_DIR / "vendor", filename)


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


@app.get("/api/rates/evidence")
def rates_evidence():
    try:
        limit = int(request.args.get("limit", "100"))
    except ValueError:
        return jsonify({"error": "limit必须是整数", "disclaimer": DISCLAIMER}), 400
    return jsonify(load_evidence(limit))


@app.get("/api/rates/reviews")
def rates_reviews():
    return jsonify({"reviews": load_reviews(), "append_only": True, "disclaimer": DISCLAIMER})


@app.get("/api/rates/demo-cases")
def rates_demo_cases():
    return jsonify(load_demo_cases())


@app.get("/api/rates/report")
def rates_report():
    response = app.response_class(load_report(), mimetype="text/markdown; charset=utf-8")
    response.headers["Content-Disposition"] = 'attachment; filename="alphalens-rates-report.md"'
    return response


@app.post("/api/rates/extract-file")
def rates_extract_file():
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return jsonify({"error": "请选择TXT、Markdown或PDF文件", "disclaimer": DISCLAIMER}), 400
    suffix = Path(uploaded.filename).suffix.lower()
    if suffix not in {".txt", ".md", ".pdf"}:
        return jsonify({"error": "仅支持TXT、Markdown和PDF文件", "disclaimer": DISCLAIMER}), 400
    payload = uploaded.read(10 * 1024 * 1024 + 1)
    if len(payload) > 10 * 1024 * 1024:
        return jsonify({"error": "文件不能超过10MB", "disclaimer": DISCLAIMER}), 413
    try:
        if suffix == ".pdf":
            reader = PdfReader(io.BytesIO(payload))
            content = "\n".join((page.extract_text() or "").strip() for page in reader.pages).strip()
        else:
            content = payload.decode("utf-8-sig").strip()
    except (UnicodeDecodeError, ValueError, OSError) as exc:
        return jsonify({"error": f"文件解析失败：{exc}", "disclaimer": DISCLAIMER}), 400
    if not content:
        return jsonify({"error": "文件没有可提取的正文；扫描版PDF需先完成OCR", "disclaimer": DISCLAIMER}), 400
    return jsonify({
        "filename": Path(uploaded.filename).name,
        "content": content[:120000],
        "characters": len(content),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "truncated": len(content) > 120000,
        "disclaimer": DISCLAIMER,
    })


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
