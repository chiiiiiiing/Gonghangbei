"""Flask service for the AlphaLens bank-rates submission."""

from __future__ import annotations

import hashlib
import io
import os
import sys
import time
from collections import OrderedDict, defaultdict, deque
from ipaddress import ip_address
from pathlib import Path
from threading import Lock

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
app.config["MAX_CONTENT_LENGTH"] = 11 * 1024 * 1024

_RATE_LIMITS: dict[str, OrderedDict[str, deque[float]]] = defaultdict(OrderedDict)
_RATE_LIMIT_LOCK = Lock()
_MAX_RATE_LIMIT_KEYS = 4096


def _client_address() -> str:
    remote = request.remote_addr or "unknown"
    try:
        from_loopback_proxy = ip_address(remote).is_loopback
    except ValueError:
        from_loopback_proxy = False
    if from_loopback_proxy:
        # nginx overwrites X-Real-IP. X-Forwarded-For may contain a
        # client-supplied first hop, so it must not be used for rate limiting.
        real_ip = request.headers.get("X-Real-IP", "").strip()
        try:
            return str(ip_address(real_ip)) if real_ip else remote
        except ValueError:
            return remote
    return remote


def _rate_limit(bucket: str, limit: int, window_seconds: int):
    now = time.monotonic()
    key = _client_address()
    with _RATE_LIMIT_LOCK:
        clients = _RATE_LIMITS[bucket]
        attempts = clients.get(key)
        if attempts is None:
            if len(clients) >= _MAX_RATE_LIMIT_KEYS:
                clients.popitem(last=False)
            attempts = deque()
            clients[key] = attempts
        else:
            clients.move_to_end(key)
        while attempts and attempts[0] <= now - window_seconds:
            attempts.popleft()
        if len(attempts) >= limit:
            retry_after = max(1, int(window_seconds - (now - attempts[0])))
            response = jsonify({"error": "请求过于频繁，请稍后重试", "disclaimer": DISCLAIMER})
            response.headers["Retry-After"] = str(retry_after)
            return response, 429
        attempts.append(now)
    return None


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
    if as_of:
        limited = _rate_limit("historical_forecast", 6, 60)
        if limited:
            return limited
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
    limited = _rate_limit("extract_file", 12, 60)
    if limited:
        return limited
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
    limited = _rate_limit("analyze", 12, 60)
    if limited:
        return limited
    try:
        payload = request.get_json(silent=True) or {}
        if len(str(payload.get("content", ""))) > 120000:
            raise ValueError("正文不能超过120000个字符")
        result = analyze_document(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc), "disclaimer": DISCLAIMER}), 400
    return jsonify(result)


@app.post("/api/rates/review")
def rates_review():
    limited = _rate_limit("review", 30, 600)
    if limited:
        return limited
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


@app.errorhandler(413)
def request_too_large(_error):
    if request.path.startswith("/api/"):
        return jsonify({"error": "请求体超过11MB服务上限", "disclaimer": DISCLAIMER}), 413
    return "Request entity too large", 413


if __name__ == "__main__":
    host = os.getenv("ALPHALENS_HOST", "127.0.0.1")
    port = int(os.getenv("ALPHALENS_PORT", "8701"))
    app.run(host=host, port=port, debug=False)
