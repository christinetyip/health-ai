import os
import sqlite3
import json
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

# Local timezone (Europe/Amsterdam)
try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("Europe/Amsterdam")
except ImportError:
    LOCAL_TZ = timezone(timedelta(hours=2))  # fallback CEST

DB_PATH = os.environ.get("GADGETBRIDGE_DB", "/data/Gadgetbridge.db")
API_KEY = os.environ.get("HEALTH_API_KEY", "")
PORT = int(os.environ.get("PORT", "3456"))
BIND_HOST = os.environ.get("BIND_HOST", "0.0.0.0")


def get_db():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def parse_date(date_str):
    """Parse a date string to start/end timestamps."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    start_ts = int(dt.timestamp())
    end_ts = int((dt + timedelta(days=1)).timestamp())
    return start_ts, end_ts


def ts_to_str(ts):
    """Convert unix timestamp to readable string in local timezone."""
    if ts is None or ts == 0:
        return None
    # Handle millisecond timestamps
    if ts > 1e12:
        ts = ts / 1000
    utc_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    local_dt = utc_dt.astimezone(LOCAL_TZ)
    return local_dt.strftime("%Y-%m-%d %H:%M")


# --- Tool implementations ---

def _sleep_ts_to_seconds(ts):
    """Convert sleep timestamp to seconds — handles both ms and s formats."""
    if ts is None:
        return None
    if ts > 1e12:  # milliseconds
        return ts / 1000
    return ts


def _is_night_sleep(row):
    """Check if a sleep session is night sleep (not a nap).
    Night sleep: started between 20:00-04:00 AND longer than 3 hours."""
    if not row:
        return False
    try:
        ts = _sleep_ts_to_seconds(row["TIMESTAMP"])
        if ts is None or ts < 1700000000:
            return False
        utc_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        local_dt = utc_dt.astimezone(LOCAL_TZ)
        hour = local_dt.hour
        duration = row["TOTAL_DURATION"] or 0
        # Night sleep starts between 20:00-04:00 and is > 3 hours
        return (hour >= 20 or hour <= 4) and duration > 180
    except (ValueError, OSError, OverflowError):
        return False


def get_sleep(date=None):
    """Get sleep data. If no date, returns most recent NIGHT sleep (not naps)."""
    db = get_db()
    try:
        if date:
            start_ts, end_ts = parse_date(date)
            evening_before = start_ts - 12 * 3600
            # Query both s and ms timestamp ranges
            rows = db.execute(
                """SELECT * FROM XIAOMI_SLEEP_TIME_SAMPLE
                   WHERE (TIMESTAMP BETWEEN ? AND ?) OR (TIMESTAMP BETWEEN ? AND ?)
                   ORDER BY TIMESTAMP DESC""",
                (evening_before, end_ts, evening_before * 1000, end_ts * 1000)
            ).fetchall()
            # Find the first night sleep session
            row = None
            for r in rows:
                if _is_night_sleep(r):
                    row = r
                    break
            # Fallback to most recent if no night sleep found
            if not row and rows:
                row = rows[0]
        else:
            # Get recent sessions and find the most recent night sleep
            rows = db.execute(
                """SELECT * FROM XIAOMI_SLEEP_TIME_SAMPLE
                   ORDER BY TIMESTAMP DESC LIMIT 10"""
            ).fetchall()
            row = None
            for r in rows:
                if _is_night_sleep(r):
                    row = r
                    break
            if not row and rows:
                row = rows[0]

        if not row:
            return {"error": "No sleep data found", "date": date}

        sleep_start = row["TIMESTAMP"]
        # Determine if timestamps are in ms or s for stage query range
        stage_range = 14 * 3600 * 1000 if sleep_start > 1e12 else 14 * 3600

        # Get sleep stages for this session
        stages = db.execute(
            """SELECT * FROM XIAOMI_SLEEP_STAGE_SAMPLE
               WHERE TIMESTAMP BETWEEN ? AND ?
               ORDER BY TIMESTAMP""",
            (sleep_start, sleep_start + stage_range)
        ).fetchall()

        stage_names = {0: "awake", 1: "light", 2: "deep", 3: "rem"}

        # Find today's naps only (sleep sessions from today that are NOT night sleep)
        today_start = int(datetime.now(LOCAL_TZ).replace(hour=0, minute=0, second=0).timestamp())
        today_start_ms = today_start * 1000
        all_recent = db.execute(
            """SELECT * FROM XIAOMI_SLEEP_TIME_SAMPLE
               WHERE TIMESTAMP > ? OR TIMESTAMP > ? ORDER BY TIMESTAMP""",
            (today_start, today_start_ms)
        ).fetchall()
        # Filter: only naps (not night sleep), only from today, duration > 10 min
        naps = []
        for r in all_recent:
            ts_s = _sleep_ts_to_seconds(r["TIMESTAMP"])
            if ts_s and ts_s >= today_start and not _is_night_sleep(r) and (r["TOTAL_DURATION"] or 0) > 10:
                naps.append(r)
        nap_total_min = sum((r["TOTAL_DURATION"] or 0) for r in naps)

        # Total sleep in last 24h (night + naps) for sleep debt
        night_min = row["TOTAL_DURATION"] or 0
        total_with_naps = night_min + nap_total_min

        return {
            "date": ts_to_str(sleep_start),
            "wakeup_time": ts_to_str(row["WAKEUP_TIME"]),
            "total_minutes": row["TOTAL_DURATION"],
            "deep_sleep_minutes": row["DEEP_SLEEP_DURATION"],
            "light_sleep_minutes": row["LIGHT_SLEEP_DURATION"],
            "rem_sleep_minutes": row["REM_SLEEP_DURATION"],
            "awake_minutes": row["AWAKE_DURATION"],
            "total_hours": round((row["TOTAL_DURATION"] or 0) / 60, 1),
            "deep_sleep_hours": round((row["DEEP_SLEEP_DURATION"] or 0) / 60, 1),
            "light_sleep_hours": round((row["LIGHT_SLEEP_DURATION"] or 0) / 60, 1),
            "rem_sleep_hours": round((row["REM_SLEEP_DURATION"] or 0) / 60, 1),
            "naps_today": [
                {
                    "start": ts_to_str(n["TIMESTAMP"]),
                    "end": ts_to_str(n["WAKEUP_TIME"]),
                    "duration_minutes": n["TOTAL_DURATION"],
                    "duration_hours": round((n["TOTAL_DURATION"] or 0) / 60, 1),
                }
                for n in naps
            ],
            "total_with_naps_hours": round(total_with_naps / 60, 1),
            "sleep_debt_hours": round((total_with_naps - 420) / 60, 1),  # 420 min = 7h target
            "stages": [
                {
                    "time": ts_to_str(s["TIMESTAMP"]),
                    "stage": stage_names.get(s["STAGE"], f"unknown({s['STAGE']})")
                }
                for s in stages
            ]
        }
    finally:
        db.close()


def get_sleep_history(days=7):
    """Get sleep history for the last N days."""
    db = get_db()
    try:
        cutoff_s = int((datetime.now() - timedelta(days=days)).timestamp())
        # Sleep timestamps can be in ms or s — query both ranges
        cutoff_ms = cutoff_s * 1000
        rows = db.execute(
            """SELECT * FROM XIAOMI_SLEEP_TIME_SAMPLE
               WHERE (TIMESTAMP > ? OR TIMESTAMP > ?)
               ORDER BY TIMESTAMP""",
            (cutoff_s, cutoff_ms)
        ).fetchall()

        return {
            "days_requested": days,
            "sessions_found": len(rows),
            "sessions": [
                {
                    "date": ts_to_str(r["TIMESTAMP"]),
                    "wakeup_time": ts_to_str(r["WAKEUP_TIME"]),
                    "total_hours": round((r["TOTAL_DURATION"] or 0) / 60, 1),
                    "deep_sleep_hours": round((r["DEEP_SLEEP_DURATION"] or 0) / 60, 1),
                    "light_sleep_hours": round((r["LIGHT_SLEEP_DURATION"] or 0) / 60, 1),
                    "rem_sleep_hours": round((r["REM_SLEEP_DURATION"] or 0) / 60, 1),
                    "awake_minutes": r["AWAKE_DURATION"],
                    "is_nap": not _is_night_sleep(r),
                }
                for r in rows
            ]
        }
    finally:
        db.close()


def get_daily_summary(date=None):
    """Get daily summary (steps, HR, stress, SpO2). If no date, returns most recent."""
    db = get_db()
    try:
        if date:
            start_ts, end_ts = parse_date(date)
            # Daily summary timestamps are in milliseconds
            row = db.execute(
                """SELECT * FROM XIAOMI_DAILY_SUMMARY_SAMPLE
                   WHERE TIMESTAMP BETWEEN ? AND ?
                   ORDER BY TIMESTAMP DESC LIMIT 1""",
                (start_ts * 1000, end_ts * 1000)
            ).fetchone()
        else:
            row = db.execute(
                """SELECT * FROM XIAOMI_DAILY_SUMMARY_SAMPLE
                   ORDER BY TIMESTAMP DESC LIMIT 1"""
            ).fetchone()

        if not row:
            return {"error": "No daily summary found", "date": date}

        return {
            "date": ts_to_str(row["TIMESTAMP"]),
            "steps": row["STEPS"],
            "calories": row["CALORIES"],
            "heart_rate": {
                "resting": row["HR_RESTING"],
                "average": row["HR_AVG"],
                "max": row["HR_MAX"],
                "max_time": ts_to_str(row["HR_MAX_TS"]),
                "min": row["HR_MIN"],
                "min_time": ts_to_str(row["HR_MIN_TS"]),
            },
            "stress": {
                "average": row["STRESS_AVG"] if row["STRESS_AVG"] not in (0, 255) else None,
                "max": row["STRESS_MAX"] if row["STRESS_MAX"] not in (0, 255) else None,
                "min": row["STRESS_MIN"] if row["STRESS_MIN"] not in (0, 255) else None,
            },
            "spo2": {
                "average": row["SPO2_AVG"] if row["SPO2_AVG"] and 70 <= row["SPO2_AVG"] <= 100 else None,
                "max": row["SPO2_MAX"] if row["SPO2_MAX"] and 70 <= row["SPO2_MAX"] <= 100 else None,
                "min": row["SPO2_MIN"] if row["SPO2_MIN"] and 70 <= row["SPO2_MIN"] <= 100 else None,
            },
            "standing_hours": bin(row["STANDING"]).count("1") if row["STANDING"] else None,
            "training_load": {
                "day": row["TRAINING_LOAD_DAY"],
                "week": row["TRAINING_LOAD_WEEK"],
                "level": row["TRAINING_LOAD_LEVEL"],
            },
            "vitality": {
                "current": row["VITALITY_CURRENT"],
                "increase_light": row["VITALITY_INCREASE_LIGHT"],
                "increase_moderate": row["VITALITY_INCREASE_MODERATE"],
                "increase_high": row["VITALITY_INCREASE_HIGH"],
            },
        }
    finally:
        db.close()


def get_daily_history(days=7):
    """Get daily summaries for the last N days."""
    db = get_db()
    try:
        cutoff = int((datetime.now() - timedelta(days=days)).timestamp()) * 1000
        rows = db.execute(
            """SELECT * FROM XIAOMI_DAILY_SUMMARY_SAMPLE
               WHERE TIMESTAMP > ?
               ORDER BY TIMESTAMP""",
            (cutoff,)
        ).fetchall()

        return {
            "days_requested": days,
            "days_found": len(rows),
            "days": [
                {
                    "date": ts_to_str(r["TIMESTAMP"]),
                    "steps": r["STEPS"],
                    "calories": r["CALORIES"],
                    "hr_resting": r["HR_RESTING"],
                    "hr_avg": r["HR_AVG"],
                    "stress_avg": r["STRESS_AVG"],
                    "spo2_avg": r["SPO2_AVG"],
                }
                for r in rows
            ]
        }
    finally:
        db.close()


def get_heart_rate(date=None, minutes=60):
    """Get heart rate samples. Defaults to last 60 minutes or a specific date."""
    db = get_db()
    try:
        if date:
            start_ts, end_ts = parse_date(date)
        else:
            end_ts = int(datetime.now().timestamp())
            start_ts = end_ts - (minutes * 60)

        rows = db.execute(
            """SELECT TIMESTAMP, HEART_RATE FROM XIAOMI_ACTIVITY_SAMPLE
               WHERE TIMESTAMP BETWEEN ? AND ? AND HEART_RATE > 0
               ORDER BY TIMESTAMP""",
            (start_ts, end_ts)
        ).fetchall()

        samples = [
            {"time": ts_to_str(r["TIMESTAMP"]), "bpm": r["HEART_RATE"]}
            for r in rows
        ]

        bpms = [r["HEART_RATE"] for r in rows]
        return {
            "date": date or "last_" + str(minutes) + "_minutes",
            "sample_count": len(samples),
            "average_bpm": round(sum(bpms) / len(bpms), 1) if bpms else None,
            "max_bpm": max(bpms) if bpms else None,
            "min_bpm": min(bpms) if bpms else None,
            "samples": samples,
        }
    finally:
        db.close()


def get_stress(date=None, minutes=60):
    """Get stress samples. Defaults to last 60 minutes or a specific date."""
    db = get_db()
    try:
        if date:
            start_ts, end_ts = parse_date(date)
        else:
            end_ts = int(datetime.now().timestamp())
            start_ts = end_ts - (minutes * 60)

        rows = db.execute(
            """SELECT TIMESTAMP, STRESS FROM XIAOMI_ACTIVITY_SAMPLE
               WHERE TIMESTAMP BETWEEN ? AND ? AND STRESS IS NOT NULL AND STRESS > 0
               ORDER BY TIMESTAMP""",
            (start_ts, end_ts)
        ).fetchall()

        samples = [
            {"time": ts_to_str(r["TIMESTAMP"]), "stress": r["STRESS"]}
            for r in rows
        ]

        values = [r["STRESS"] for r in rows]
        return {
            "date": date or "last_" + str(minutes) + "_minutes",
            "sample_count": len(samples),
            "average": round(sum(values) / len(values), 1) if values else None,
            "max": max(values) if values else None,
            "min": min(values) if values else None,
            "samples": samples,
        }
    finally:
        db.close()


def get_steps(date=None, minutes=60):
    """Get step data. Defaults to last 60 minutes or a specific date."""
    db = get_db()
    try:
        if date:
            start_ts, end_ts = parse_date(date)
        else:
            end_ts = int(datetime.now().timestamp())
            start_ts = end_ts - (minutes * 60)

        rows = db.execute(
            """SELECT TIMESTAMP, STEPS FROM XIAOMI_ACTIVITY_SAMPLE
               WHERE TIMESTAMP BETWEEN ? AND ? AND STEPS > 0
               ORDER BY TIMESTAMP""",
            (start_ts, end_ts)
        ).fetchall()

        samples = [
            {"time": ts_to_str(r["TIMESTAMP"]), "steps": r["STEPS"]}
            for r in rows
        ]

        total = sum(r["STEPS"] for r in rows)
        return {
            "date": date or "last_" + str(minutes) + "_minutes",
            "total_steps": total,
            "sample_count": len(samples),
            "samples": samples,
        }
    finally:
        db.close()


def get_activity_raw(date=None, hours=1):
    """Get raw activity samples with all fields. For detailed analysis."""
    db = get_db()
    try:
        if date:
            start_ts, end_ts = parse_date(date)
        else:
            end_ts = int(datetime.now().timestamp())
            start_ts = end_ts - (hours * 3600)

        rows = db.execute(
            """SELECT * FROM XIAOMI_ACTIVITY_SAMPLE
               WHERE TIMESTAMP BETWEEN ? AND ?
               ORDER BY TIMESTAMP""",
            (start_ts, end_ts)
        ).fetchall()

        return {
            "date": date or f"last_{hours}_hours",
            "sample_count": len(rows),
            "samples": [
                {
                    "time": ts_to_str(r["TIMESTAMP"]),
                    "steps": r["STEPS"],
                    "heart_rate": r["HEART_RATE"] if r["HEART_RATE"] > 0 else None,
                    "stress": r["STRESS"],
                    "spo2": r["SPO2"],
                    "calories": r["ACTIVE_CALORIES"],
                    "distance_meters": round(r["DISTANCE_CM"] / 100, 1) if r["DISTANCE_CM"] else 0,
                    "raw_intensity": r["RAW_INTENSITY"],
                    "raw_kind": r["RAW_KIND"],
                }
                for r in rows
            ]
        }
    finally:
        db.close()


# --- MCP Protocol ---

TOOLS = {
    "get_sleep": {
        "description": "Get sleep data for a specific night or most recent. Returns sleep duration, stages (deep/light/REM/awake), and timestamps. Use for sleep quality analysis, pattern detection, and coaching.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format. Returns sleep for the night ending on this date. Omit for most recent."}
            }
        },
        "fn": get_sleep,
    },
    "get_sleep_history": {
        "description": "Get sleep history over multiple days. Use for trend analysis, identifying patterns (e.g., sleep debt, consistency), and coaching on sleep habits.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Number of days to look back. Default 7.", "default": 7}
            }
        },
        "fn": get_sleep_history,
    },
    "get_daily_summary": {
        "description": "Get a day's summary: steps, heart rate (resting/avg/max/min), stress levels, SpO2, calories, standing hours, training load, vitality score. Use for daily check-ins and overall health assessment.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format. Omit for most recent."}
            }
        },
        "fn": get_daily_summary,
    },
    "get_daily_history": {
        "description": "Get daily summaries over multiple days. Use for trend analysis across steps, heart rate, stress, SpO2. Good for identifying patterns and building dashboards.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Number of days to look back. Default 7.", "default": 7}
            }
        },
        "fn": get_daily_history,
    },
    "get_heart_rate": {
        "description": "Get detailed heart rate samples with timestamps. Use for intra-day HR analysis, exercise detection, stress correlation, and visualizations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format. Omit to get recent data."},
                "minutes": {"type": "integer", "description": "Minutes of recent data (if no date). Default 60.", "default": 60}
            }
        },
        "fn": get_heart_rate,
    },
    "get_stress": {
        "description": "Get detailed stress level samples with timestamps. Use for stress pattern analysis, identifying triggers, and correlating with activities or sleep quality.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format. Omit to get recent data."},
                "minutes": {"type": "integer", "description": "Minutes of recent data (if no date). Default 60.", "default": 60}
            }
        },
        "fn": get_stress,
    },
    "get_steps": {
        "description": "Get step count data with timestamps. Use for activity analysis, movement patterns, and exercise detection.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format. Omit to get recent data."},
                "minutes": {"type": "integer", "description": "Minutes of recent data (if no date). Default 60.", "default": 60}
            }
        },
        "fn": get_steps,
    },
    "get_activity_raw": {
        "description": "Get raw activity samples with ALL fields (steps, HR, stress, SpO2, calories, distance, intensity). Use for detailed analysis, custom visualizations, pattern detection, and building dashboards. Returns per-minute granularity.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format. Omit to get recent data."},
                "hours": {"type": "integer", "description": "Hours of recent data (if no date). Default 1.", "default": 1}
            }
        },
        "fn": get_activity_raw,
    },
}


def handle_mcp_request(request_body):
    """Handle an MCP JSON-RPC request."""
    try:
        req = json.loads(request_body)
    except json.JSONDecodeError:
        return {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None}

    method = req.get("method", "")
    req_id = req.get("id")
    params = req.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "health-mcp", "version": "1.0.0"},
            },
        }

    if method == "notifications/initialized":
        return None  # No response needed

    if method == "tools/list":
        tool_list = []
        for name, tool in TOOLS.items():
            tool_list.append({
                "name": name,
                "description": tool["description"],
                "inputSchema": tool["inputSchema"],
            })
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": tool_list},
        }

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name not in TOOLS:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps({"error": f"Unknown tool: {tool_name}"})}],
                    "isError": True,
                },
            }

        try:
            result = TOOLS[tool_name]["fn"](**arguments)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
                    "isError": False,
                },
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps({"error": str(e)})}],
                    "isError": True,
                },
            }

    return {
        "jsonrpc": "2.0",
        "error": {"code": -32601, "message": f"Method not found: {method}"},
        "id": req_id,
    }


class MCPHandler(BaseHTTPRequestHandler):
    def check_auth(self):
        if not API_KEY:
            return True
        auth = self.headers.get("Authorization", "")
        if auth == f"Bearer {API_KEY}":
            return True
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"error": "Unauthorized"}).encode())
        return False

    def do_POST(self):
        if not self.check_auth():
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode()

        response = handle_mcp_request(body)
        if response is None:
            self.send_response(204)
            self.end_headers()
            return

        response_bytes = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.end_headers()
        self.wfile.write(response_bytes)

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode())
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {args[0]}")


if __name__ == "__main__":
    server = HTTPServer((BIND_HOST, PORT), MCPHandler)
    print(f"Health MCP server listening on {BIND_HOST}:{PORT}")
    print(f"Database: {DB_PATH}")
    print(f"Auth: {'API key required' if API_KEY else 'NO AUTH (not recommended)'}")
    server.serve_forever()
