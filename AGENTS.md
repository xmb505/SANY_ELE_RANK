# AGENTS.md

This file provides guidance to Qoder (qoder.com) when working with code in this repository.

## Project Overview

SANY_ELE_RANK is a night-time electricity usage anomaly detection and ranking system for college dormitories. It reads meter data collected by the companion project [SANY_check_money](https://github.com/xmb505/SANY_check_money) from a shared MySQL database, applies a spike-detection algorithm to identify abnormal power consumption during 23:00-06:00, and ranks dormitories by anomaly score (0-100).

**Language**: Chinese (all docs, comments, and UI text)

## Architecture

```
SANY_check_money (external)          SANY_ELE_RANK
┌──────────────────────┐     ┌────────────────────────────┐
│ Data collection      │     │ server/                    │
│ login.py -> get_data │────>│   Python 3 + http.server   │
│ -> data2sql.py       │     │   PyMySQL                  │
│                      │     │   Reads device & data tables│
└──────────┬───────────┘     ├────────────────────────────┤
           │                 │ web/                       │
           v                 │   Vanilla HTML/CSS/JS      │
    ┌─────────────┐          │   Chart.js for graphs      │
    │ MySQL (shared)│<────────┤                            │
    │ - device    │          └────────────────────────────┘
    │ - data      │
    │ - email     │
    └─────────────┘
```

- **server/**: Python backend - RESTful API via `http.server`, connects to the same MySQL instance as SANY_check_money (port 8081 to avoid conflict with SANY_check_money's 8080)
- **web/**: Static frontend - no build step, served directly or via Nginx
- **doc/**: PROJECT.md (full project spec), ALGORITHM.md (scoring formula, test cases, known data issues)

## Core Algorithm

Input: 7 hourly kWh values (x1..x7) for 23:00-06:00. Calculates spike prominence per hour, sums them into S, then `score = min(100, S * 25)`. See `doc/ALGORITHM.md` for full derivation and test scenarios.

**Known data issue**: Meter `read_time` is not on exact hour boundaries. The data processing layer must handle time alignment, deduplication, and missing-hour interpolation before feeding into the algorithm. Details documented in `doc/ALGORITHM.md` section 8.

## Database Schema (owned by SANY_check_money, read-only for this project)

- **device**: `id` (varchar PK), `equipmentName`, `installationSite`, `equipmentType` (0=electric, 1=water), `ratio`, `rate`, `status`
- **data**: `id` (auto PK), `device_id` (FK->device), `read_time`, `total_reading` (cumulative kWh), `remainingBalance`, `unStandard` flag. Index: `idx_device_time(device_id, read_time)`

## Configuration

Config files use Python `configparser` `.ini` format (matching SANY_check_money conventions). Example templates prefixed with `example_`. Actual config files are gitignored.

- `server/config/server.ini`: MySQL connection + server port
- `server/config/algorithm.ini`: night time window, threshold multiplier, weights

## Dependencies

```bash
pip install pymysql
```

No frontend build tools required. Chart.js is vendored or loaded via CDN.

## Development Notes

- This project is in early stage: documentation is complete, implementation is in progress
- The companion project SANY_check_money is at `/home/xmb505/SANY_check_money` on the development machine - reference it for database schema details and API patterns
- Backend follows the same patterns as SANY_check_money's `server/server.py`: connection pooling, ThreadPoolExecutor, JSON responses with `Access-Control-Allow-Origin: *`
