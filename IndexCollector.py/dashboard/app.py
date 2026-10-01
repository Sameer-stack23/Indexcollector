"""Local web dashboard for IndexCollector."""

from pathlib import Path
import sys

from flask import Flask, jsonify, render_template, request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from collector_manager import CollectorManager


app = Flask(__name__)
# The dashboard accepts only tiny JSON control messages; reject unusually large
# requests before Flask keeps them in memory.
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
manager = CollectorManager()


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/status")
def status():
    return jsonify(manager.status())


@app.post("/api/start")
def start():
    payload = request.get_json(silent=True) or {}
    started, message = manager.start(
        payload.get("mode", "historical"),
        payload.get("from_date") or None,
        payload.get("to_date") or None,
    )
    return jsonify({"ok": started, "message": message, "status": manager.status()}), 200 if started else 409


@app.post("/api/stop")
def stop():
    stopped, message = manager.stop()
    return jsonify({"ok": stopped, "message": message, "status": manager.status()}), 200 if stopped else 409


if __name__ == "__main__":
    print("IndexCollector dashboard: http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
