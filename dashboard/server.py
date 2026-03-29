"""
Body Compass Dashboard — backend server
Serves the dashboard UI and provides API endpoints that:
  - Pull health data from the MCP server (same Docker bridge)
  - Read radar check-in YAML files
  - Generate AI coaching text via OpenRouter
"""

import os
import json
import glob
import re
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import URLError

# Local timezone (Europe/Amsterdam)
try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("Europe/Amsterdam")
except ImportError:
    LOCAL_TZ = timezone(timedelta(hours=2))  # fallback CET+2


PORT = int(os.environ.get("PORT", "3457"))
MCP_URL = os.environ.get("MCP_URL", "http://health-mcp:3456")
MCP_API_KEY = os.environ.get("MCP_API_KEY", "")
OPENROUTER_KEY = os.environ.get("OPENROUTER_KEY", "")
KIMI_KEY = os.environ.get("KIMI_KEY", "")
RADAR_DIR = os.environ.get("RADAR_DIR", "/radar")


# --- MCP client ---

def mcp_call(tool_name, arguments=None):
    """Call a tool on the health MCP server."""
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments or {}}
    }).encode()
    headers = {"Content-Type": "application/json"}
    if MCP_API_KEY:
        headers["Authorization"] = f"Bearer {MCP_API_KEY}"
    try:
        req = Request(f"{MCP_URL}/", data=body, headers=headers, method="POST")
        with urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            text = result.get("result", {}).get("content", [{}])[0].get("text", "{}")
            return json.loads(text)
    except Exception as e:
        return {"error": str(e)}


# --- Radar files ---

def read_radar_files(days=7):
    """Read the last N days of radar check-in YAML files."""
    entries = []
    for i in range(days):
        date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        path = os.path.join(RADAR_DIR, f"{date}.md")
        if os.path.exists(path):
            entry = parse_radar_file(path)
            if entry:
                entries.append(entry)
    return entries


def parse_radar_file(path):
    """Parse a radar YAML frontmatter file."""
    try:
        with open(path, "r") as f:
            content = f.read()
        # Extract YAML frontmatter between --- markers
        match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
        if not match:
            return None
        yaml_text = match.group(1)
        entry = {}
        for line in yaml_text.strip().split("\n"):
            if ":" in line:
                key, val = line.split(":", 1)
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                # Convert types
                if val.lower() in ("true", "yes"):
                    val = True
                elif val.lower() in ("false", "no"):
                    val = False
                else:
                    try:
                        val = float(val) if "." in val else int(val)
                    except ValueError:
                        pass
                entry[key] = val
        return entry
    except Exception:
        return None


# --- AI coaching ---

def generate_coaching(health_data, radar_data):
    """Generate all AI coaching texts using OpenRouter."""
    if not OPENROUTER_KEY and not KIMI_KEY:
        return default_coaching()

    sleep = health_data.get("sleep", {})
    daily = health_data.get("daily", {})
    sleep_history = health_data.get("sleep_history", {})
    daily_history = health_data.get("daily_history", {})
    stress_today_samples = health_data.get("stress_today_samples", {})
    weekly_stress = health_data.get("weekly_stress", [])
    weekly_summary = health_data.get("weekly_summary", {})
    today_radar = radar_data[0] if radar_data else {}

    # Build weekly summary for the prompt
    daily_days = (daily_history or {}).get("days", [])

    prompt = f"""You are an empathetic AI health coach for Christine. She has a pattern of pushing through tiredness with intellectual stimulation until she crashes. She's building self-awareness about her body signals. Never guilt, never push. Name patterns gently.

REASONING FRAMEWORK — follow this before generating any coaching text:

Before writing anything, assess the OVERALL picture across ALL dimensions:

1. PHYSICAL RECOVERY: Calculate total sleep recovery (night + naps). Compare to weekly average. Is today a catch-up day or a deficit day? What's the weekly sleep debt trajectory — improving, declining, or stable?

2. PHYSICAL ACTIVITY: Steps and exercise today vs weekly average. Is movement trending up or down? Any workouts/swimming this week?

3. STRESS & BODY SIGNALS: Stress level today vs weekly average. Heart rate patterns — elevated or normal? Any spikes correlating with specific times or activities?

4. MENTAL & EMOTIONAL STATE: Self-reported energy level and feeling from check-in. Drains and recharges — what's consuming vs restoring energy? People energy. Middle gear availability. Emotional clarity — is she naming feelings or going blank?

5. CROSS-DOMAIN PATTERNS: Does the body data confirm or contradict the self-reported state? Which dimension is the weakest link right now? What's the relationship between last night's sleep and today's energy? Are drains from check-ins correlating with stress spikes?

Your coaching tone should match the overall picture across ALL dimensions, not just one. Never react to a single metric in isolation. Always consider the full day and full week context.

Here is today's data:

SLEEP (last night):
- Total: {sleep.get('total_hours', '?')}h (healthy minimum: 7h)
- Deep: {sleep.get('deep_sleep_hours', '?')}h ({round(sleep.get('deep_sleep_minutes', 0) / max(sleep.get('total_minutes', 1), 1) * 100)}% — healthy: 15-25%)
- Light: {sleep.get('light_sleep_hours', '?')}h
- REM: {sleep.get('rem_sleep_hours', '?')}h ({round(sleep.get('rem_sleep_minutes', 0) / max(sleep.get('total_minutes', 1), 1) * 100)}% — healthy: 20-25%)
- Bedtime: {sleep.get('date', '?')}, Wakeup: {sleep.get('wakeup_time', '?')}
- Total with naps: {sleep.get('total_with_naps_hours', '?')}h
- Sleep debt (including naps): {sleep.get('sleep_debt_hours', '?')}h
- Naps today: {json.dumps(sleep.get('naps_today', []), default=str) if sleep.get('naps_today') else 'none'}

DAILY SUMMARY (today):
- Steps: {daily.get('steps', '?')}
- Resting HR: {daily.get('heart_rate', {}).get('resting', '?')} (healthy: 55-75)
- Stress avg: {daily.get('stress', {}).get('average', 'no data')}

TODAY'S STRESS TIMELINE ({(stress_today_samples or {}).get('sample_count', 0)} readings):
- Average: {(stress_today_samples or {}).get('average', 'no data')}
- Peak: {(stress_today_samples or {}).get('max', 'no data')}
- Low: {(stress_today_samples or {}).get('min', 'no data')}

CHECK-IN (self-reported today):
- Energy: {today_radar.get('energy', 'no check-in')}/5
- Feeling: {today_radar.get('feeling', 'no check-in')}
- People energy: {today_radar.get('people', 'no check-in')}
- Drain: {today_radar.get('drain', 'none reported')}
- Recharge: {today_radar.get('recharge', 'none reported')}
- Middle gear available: {today_radar.get('middle_gear_available', 'no check-in')}
- Sleep flag: {today_radar.get('sleep_flag', False)}

=== WEEKLY SLEEP SUMMARY (use this for the big picture — this is the most important context) ===
- Nights this week: {weekly_summary.get('num_nights', '?')}
- Total night sleep: {weekly_summary.get('total_night_hours', '?')}h (avg {weekly_summary.get('avg_night_hours', '?')}h/night)
- Weekly target: {weekly_summary.get('weekly_target_hours', '?')}h (7h x {weekly_summary.get('num_nights', '?')} nights)
- Weekly night debt: {weekly_summary.get('weekly_night_debt', '?')}h
- Total nap hours this week: {weekly_summary.get('total_nap_hours', '?')}h
- Weekly debt including naps: {weekly_summary.get('weekly_debt_with_naps', '?')}h
- Trajectory: {weekly_summary.get('trajectory', '?')}

=== WEEKLY DATA (use this for trend analysis, pattern detection, and weekly insights) ===

ALL CHECK-INS THIS WEEK (feelings, drains, recharges, energy levels):
{json.dumps(radar_data[:7], indent=2, default=str)}

SLEEP HISTORY (7 nights — look for patterns: consecutive short nights, declining quality, bedtime drift):
{json.dumps(sleep_history.get('sessions', []), indent=2, default=str)}

DAILY SUMMARIES THIS WEEK (steps, resting HR, stress per day — look for trends):
{json.dumps(daily_days, indent=2, default=str)}

STRESS PER DAY THIS WEEK (daily averages, peaks — look for which days are highest):
{json.dumps(weekly_stress, indent=2, default=str)}

Generate a JSON object with these exact keys, each containing a short coaching text (1-3 sentences, warm and direct). Be SPECIFIC — use actual numbers, times, and names from the data. End each with a concrete action including specific suggestions for activities and exact times where possible.

STRICT FORMATTING RULES — MUST FOLLOW:
- NEVER use the name "Christine" or any name in fields 3 through 11. Only use her name once, in field 2 (hero_advice). All other fields must use "you" and "your" only. This is mandatory.
- The sleep_coach text (field 4) must be COMPLETELY DIFFERENT from mind_body_gap (field 3). sleep_coach talks about sleep architecture, stage distribution, efficiency. mind_body_gap talks about the gap between subjective feeling and objective body data. Do NOT repeat similar messages in both.

1. "hero_phrase" — 3-5 word phrase like "Pace yourself today." or "You've got this." Based on overall state. Custom and different each time.
2. "hero_advice" — 1-2 sentences of personalized daily advice combining body + mind signals. End with one clear action.
3. "mind_body_gap" — If body signals and self-reported energy disagree, explain the gap. If they agree, say so warmly. Reference her specific pattern of masking depletion with intellectual stimulation if relevant. CRITICAL: Reference SPECIFIC drains by name (e.g., "to-do list pressure", "kids after dinner"). Quote the self-reported feeling word. State exactly how many minutes/hours sleep was below the 7h target. Name what's masking the depletion (e.g., "learning" as recharge). End with a concrete action like "tonight's sleep matters" or "bed by 23:30."
4. "sleep_coach" — Focus on sleep ARCHITECTURE and QUALITY (this must be different from mind_body_gap). Talk about: stage ratios and what they mean for recovery, sleep efficiency, whether REM/deep were well-distributed or concentrated, how this night compares to the week's average. Give a unique insight the user wouldn't get from just looking at the numbers. End with a SPECIFIC time-based bedtime suggestion (calculate from desired wake time minus 7.5h).
5. "depletion_watch" — If any indicator is amber, what to watch out for. If all green, gentle encouragement. Name any amber/red indicators specifically. Say what to watch for next. If all green, name the one closest to turning amber.
6. "weather_insight" — Analyze the pattern across recent check-in feelings and drains. Be specific about what correlates with what. Reference specific feelings by name and what preceded them. Identify the specific drain that correlates with tiredness.
7. "stress_coach" — What the stress pattern means and one actionable tip. What stress level means practically. One specific action with a time/duration.
8. "movement_coach" — Gentle, non-judgmental movement encouragement. Reference exact step count. Suggest a specific activity and duration.
9. "evening_pattern" — Pattern across recent evenings. What predicts good vs depleted evenings. Name the specific drains by name.
10. "weekly_quote" — One data-driven correlation insight across body + mind data. Use actual numbers from the data.
11. "weekly_action" — One specific, actionable recommendation for the coming days based on all the data. Include exact timing (e.g., "set a bedtime alarm for 23:00 for the next 3 nights").
12. "nap_coach" — ONLY include this field if there are naps today (check the naps data above). If no naps, do NOT include this key at all. If there are naps: comment on the nap quality, how it affects total recovery and sleep debt, and frame it positively ("your body asked for rest and you listened"). Mention the combined night+nap total and what it means for the rest of the day.

Return ONLY valid JSON, no markdown, no explanation."""

    try:
        # Try Kimi first, then OpenRouter
        if KIMI_KEY:
            api_url = "https://api.moonshot.ai/v1/chat/completions"
            api_key = KIMI_KEY
            model = "moonshot-v1-auto"
        else:
            api_url = "https://openrouter.ai/api/v1/chat/completions"
            api_key = OPENROUTER_KEY
            model = "google/gemma-3-27b-it:free"

        body = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 2000,
        }).encode()
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        if not KIMI_KEY:
            headers["HTTP-Referer"] = "http://body-compass.local"
        req = Request(api_url, data=body, headers=headers, method="POST")
        with urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
            msg = result["choices"][0]["message"]
            # Some models put content in "reasoning" instead of "content"
            text = msg.get("content") or msg.get("reasoning") or ""
            if not text:
                details = msg.get("reasoning_details", [])
                if details:
                    text = details[0].get("text", "")
            if not text:
                raise ValueError("Model returned empty response")
            text = text.strip()
            if text.startswith("```"):
                text = re.sub(r"^```(?:json)?\s*", "", text)
                text = re.sub(r"\s*```$", "", text)
            return json.loads(text)
    except Exception as e:
        coaching = default_coaching()
        coaching["_error"] = str(e)
        return coaching


def default_coaching():
    """Fallback coaching text when AI is unavailable."""
    return {
        "hero_phrase": "Check in with yourself.",
        "hero_advice": "Take a moment to notice how your body feels right now, separate from what your mind is telling you.",
        "mind_body_gap": "No AI analysis available right now. Trust what your body is telling you.",
        "sleep_coach": "Review your sleep data and notice how you feel compared to the numbers.",
        "depletion_watch": "Keep an eye on your signals today.",
        "weather_insight": "Look at your recent feelings — do you notice any patterns?",
        "stress_coach": "If stress feels elevated, try a 2-minute breathing pause.",
        "movement_coach": "Any movement counts. Even a short walk helps.",
        "evening_pattern": "Notice your energy level during evening time with the kids.",
        "weekly_quote": "Patterns become visible over time. Keep checking in.",
        "weekly_action": "Tonight, try getting to bed 15 minutes earlier than usual.",
    }


# --- HTTP Handler ---

class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.serve_file("/app/index.html", "text/html")
        elif self.path == "/api/dashboard":
            self.serve_dashboard_data()
        elif self.path == "/health":
            self.send_json({"status": "ok"})
        else:
            self.send_response(404)
            self.end_headers()

    def serve_file(self, path, content_type):
        try:
            with open(path, "r") as f:
                content = f.read().encode()
            self.send_response(200)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()

    def serve_dashboard_data(self):
        """Aggregate all data sources and return dashboard JSON."""
        # Fetch from MCP server
        sleep = mcp_call("get_sleep")
        daily = mcp_call("get_daily_summary")
        sleep_history = mcp_call("get_sleep_history", {"days": 7})
        daily_history = mcp_call("get_daily_history", {"days": 7})
        # Steps since midnight local time
        now_local = datetime.now(LOCAL_TZ)
        midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        minutes_since_midnight = int((now_local - midnight).total_seconds() / 60)
        steps = mcp_call("get_steps", {"minutes": minutes_since_midnight or 1})
        # Full day stress data (for chart + evening energy calculation)
        stress_today = mcp_call("get_stress", {"minutes": minutes_since_midnight or 1})
        # Also get yesterday's stress for evening energy (18:00-21:00 window)
        stress_yesterday = mcp_call("get_stress", {"date": (now_local - timedelta(days=1)).strftime("%Y-%m-%d")})

        # Read radar files
        radar = read_radar_files(7)

        # Fetch weekly stress summaries for each day (for AI coaching)
        weekly_stress = []
        for i in range(7):
            day = (now_local - timedelta(days=i)).strftime("%Y-%m-%d")
            day_stress = mcp_call("get_stress", {"date": day})
            if day_stress and not day_stress.get("error"):
                weekly_stress.append({
                    "date": day,
                    "sample_count": day_stress.get("sample_count", 0),
                    "average": day_stress.get("average"),
                    "max": day_stress.get("max"),
                    "min": day_stress.get("min"),
                })

        # Calculate weekly sleep debt
        sessions_all = (sleep_history or {}).get("sessions", [])
        night_sessions = [s for s in sessions_all if not s.get("is_nap")]
        nap_sessions = [s for s in sessions_all if s.get("is_nap")]
        total_night_hours = sum(s.get("total_hours", 0) for s in night_sessions)
        total_nap_hours = sum(s.get("total_hours", 0) for s in nap_sessions)
        num_nights = len(night_sessions)
        weekly_target = num_nights * 7
        weekly_night_debt = round(total_night_hours - weekly_target, 1)
        weekly_debt_with_naps = round(weekly_night_debt + total_nap_hours, 1)
        avg_night = round(total_night_hours / num_nights, 1) if num_nights > 0 else 0

        # Determine trajectory
        if num_nights >= 2:
            recent_half = night_sessions[len(night_sessions)//2:]
            older_half = night_sessions[:len(night_sessions)//2]
            recent_avg = sum(s.get("total_hours", 0) for s in recent_half) / len(recent_half)
            older_avg = sum(s.get("total_hours", 0) for s in older_half) / len(older_half)
            trajectory = "improving" if recent_avg > older_avg + 0.3 else ("declining" if recent_avg < older_avg - 0.3 else "stable")
        else:
            trajectory = "not enough data"

        weekly_summary = {
            "num_nights": num_nights,
            "total_night_hours": total_night_hours,
            "avg_night_hours": avg_night,
            "weekly_target_hours": weekly_target,
            "weekly_night_debt": weekly_night_debt,
            "total_nap_hours": total_nap_hours,
            "weekly_debt_with_naps": weekly_debt_with_naps,
            "trajectory": trajectory,
        }

        # Generate AI coaching
        health_data = {
            "sleep": sleep,
            "daily": daily,
            "sleep_history": sleep_history,
            "daily_history": daily_history,
            "stress_today_samples": stress_today,
            "weekly_stress": weekly_stress,
            "weekly_summary": weekly_summary,
        }
        coaching = generate_coaching(health_data, radar)

        # Calculate depletion radar
        depletion = calculate_depletion(sleep, daily, steps, radar)

        # Extract timestamps for "last updated" display (local time)
        now_str = now_local.strftime("%Y-%m-%d %H:%M")
        # Latest stress timestamp
        stress_ts = "unknown"
        stress_samples = (stress_today or {}).get("samples", [])
        if stress_samples:
            stress_ts = stress_samples[-1].get("time", "unknown") + " today"
        elif daily.get("date"):
            stress_ts = daily.get("date") + " (daily summary)"

        sleep_period = f"{sleep.get('date', '?')} → {sleep.get('wakeup_time', '?')}"
        timestamps = {
            "sleep": f"Night of {sleep_period}",
            "daily": daily.get("date", "unknown"),
            "steps": now_str + f" ({steps.get('total_steps', 0)} steps since midnight)",
            "stress": stress_ts,
            "stress_note": "Stress is measured continuously. This shows the latest synced reading. Data syncs from your band every ~1 hour.",
            "radar": radar[0].get("date", "unknown") if radar else "no check-ins",
            "dashboard_generated": now_str,
        }

        dashboard = {
            "date": datetime.now().strftime("%A, %b %d"),
            "sleep": sleep,
            "daily": daily,
            "sleep_history": sleep_history,
            "daily_history": daily_history,
            "steps": steps,
            "radar": radar,
            "coaching": coaching,
            "depletion": depletion,
            "stress_today": stress_today,
            "stress_yesterday": stress_yesterday,
            "weekly_summary": weekly_summary,
            "timestamps": timestamps,
        }
        self.send_json(dashboard)

    def send_json(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {args[0]}")


def calculate_depletion(sleep, daily, steps, radar):
    """Calculate depletion radar status."""
    indicators = []

    # Sleep
    total_h = sleep.get("total_hours", 0) or 0
    sleep_status = "green" if total_h >= 6.5 else ("yellow" if total_h >= 5.5 else "red")
    indicators.append({"name": "Sleep", "value": f"{total_h}h", "status": sleep_status, "trend": "stable"})

    # Stress
    stress_avg = (daily.get("stress", {}) or {}).get("average")
    if stress_avg and stress_avg < 40:
        stress_status = "green"
    elif stress_avg and stress_avg < 60:
        stress_status = "yellow"
    elif stress_avg:
        stress_status = "red"
    else:
        stress_status = "green"
        stress_avg = None
    indicators.append({"name": "Stress", "value": "low" if not stress_avg else str(stress_avg), "status": stress_status, "trend": "stable"})

    # Movement
    total_steps = (steps.get("total_steps", 0)) or 0
    move_status = "green" if total_steps >= 4000 else ("yellow" if total_steps >= 1000 else "red")
    indicators.append({"name": "Movement", "value": str(total_steps), "status": move_status, "trend": "low" if total_steps < 2000 else "stable"})

    # Mind signals from radar
    today = radar[0] if radar else {}
    energy = today.get("energy", 3)
    energy_status = "green" if energy >= 3 else ("yellow" if energy >= 2 else "red")
    indicators.append({"name": "Energy", "value": f"{energy}/5", "status": energy_status, "trend": "stable", "source": "check-in"})

    people = today.get("people", "neutral")
    people_status = "red" if people in ("draining", "avoiding") else "green"
    indicators.append({"name": "People", "value": str(people), "status": people_status, "trend": "stable", "source": "check-in"})

    mg = today.get("middle_gear_available", True)
    mg_status = "green" if mg else "yellow"
    indicators.append({"name": "Middle gear", "value": "yes" if mg else "no", "status": mg_status, "trend": "stable", "source": "check-in"})

    # Overall status
    yellow_count = sum(1 for i in indicators if i["status"] in ("yellow", "red"))
    if yellow_count >= 3:
        overall = "red"
    elif yellow_count >= 2:
        overall = "yellow"
    else:
        overall = "green"

    return {"overall": overall, "indicators": indicators}


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), DashboardHandler)
    print(f"Body Compass dashboard on port {PORT}")
    print(f"MCP server: {MCP_URL}")
    print(f"Radar dir: {RADAR_DIR}")
    print(f"OpenRouter: {'configured' if OPENROUTER_KEY else 'NOT configured'}")
    server.serve_forever()
