# Body Compass

Personal health dashboard that connects a Xiaomi Smart Band to AI coaching. Combines body signals (sleep, stress, heart rate, steps) with daily emotional check-ins to detect burnout patterns before they happen.

## Components

### MCP Server (`mcp-server/`)

Python HTTP server that reads the Gadgetbridge SQLite database and exposes health data via the [Model Context Protocol](https://modelcontextprotocol.io/).

**Tools:**
- `get_sleep` — Night sleep data with nap detection, stage breakdown, weekly debt
- `get_sleep_history` — Multiple nights with nap flags
- `get_daily_summary` — Steps, heart rate, stress, SpO2, standing hours
- `get_daily_history` — Multi-day summaries for trends
- `get_heart_rate` — Per-minute heart rate samples
- `get_stress` — Per-minute stress samples
- `get_steps` — Step data with timestamps
- `get_activity_raw` — All fields at per-minute granularity

### Dashboard (`dashboard/`)

Web app with AI-generated coaching. Calls the MCP server for health data and an LLM for personalized coaching text.

**Sections:**
- Today's Pulse — overall readiness with dynamic metrics
- Mind-Body Gap — detects when subjective energy disagrees with body signals
- Last Night's Sleep — stage breakdown with benchmarks + nap tracking
- Sleep Trend — time-positioned bars showing when sleep happened (not just duration)
- Depletion Radar — 6 indicators (3 body, 3 mind) with collapse prediction
- Emotional Weather — feeling words, clarity tracking, drains/recharges
- Stress & Recovery — real-time chart from band data
- Movement — steps with gentle coaching
- Evening Energy — quality during family time
- Weekly Insight — data-driven correlations and actionable recommendations

**AI Coaching:**
- 12 coaching sections generated per dashboard load
- Holistic reasoning framework — assesses physical recovery, activity, stress, mental state, and cross-domain patterns before generating any text
- Weekly cumulative sleep debt (includes naps, shows trajectory)

### Design (`UI/`)

Inspiration files for the "Prairie Health" visual aesthetic — warm earth tones, fabric texture, serif + sans-serif typography, organic breathing blob animation.

## Prerequisites

- Xiaomi Smart Band (tested with Band 10 China version)
- Android phone with [Gadgetbridge](https://gadgetbridge.org/) (from F-Droid)
- [Syncthing](https://syncthing.net/) for syncing data
- Docker
- API key for [Kimi](https://platform.moonshot.ai/) or [OpenRouter](https://openrouter.ai/)

## Built With

Built collaboratively with [Claude Code](https://claude.ai/code).
