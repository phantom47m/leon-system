---
name: geomanic
description: Connect OpenClaw to Geomanic — your privacy-first GPS tracking companion. Query travel statistics, manage waypoints, and analyze your journeys through natural language.
author: monswyk
version: 1.0.0
tags: gps, tracking, travel, statistics, waypoints, maps, privacy
---

# Geomanic

Connect [OpenClaw](https://www.getopenclaw.ai) to [Geomanic](https://geomanic.com), a privacy-first GPS tracking platform.

## What you can do

- **Query travel statistics** — distances, speeds, altitudes, country breakdown with full/part day tracking
- **Manage waypoints** — create, update, delete, list, and search GPS waypoints
- **Analyze journeys** — ask natural language questions about your travel data
- **Track countries visited** — ideal for tax residency documentation (183-day rule)

## Setup

### 1. Get your API key

Go to [geomanic.com/data](https://geomanic.com/data) and generate an MCP API key in the "MCP Integration" tile.

### 2. Configure

Set your API key after installing the skill:

```
/secrets set GEOMANIC_TOKEN gmnc_mcp_your_key_here
```

## Available tools

| Tool | Description |
|------|-------------|
| `create_waypoint` | Create a new GPS waypoint with coordinates, speed, altitude, timestamp |
| `update_waypoint` | Update an existing waypoint by ID |
| `delete_waypoint` | Delete a waypoint by ID |
| `get_waypoint` | Get a single waypoint by ID |
| `list_waypoints` | List waypoints with time range, pagination, sorting |
| `get_statistics` | Aggregated stats: distance, speed, altitude, country breakdown |
| `get_date_range` | Earliest and latest waypoint dates |

## Example prompts

- "How many countries have I visited this year?"
- "What was my total distance in January?"
- "Show me my last 10 waypoints"
- "How many days did I spend in Germany in 2025?"
- "Create a waypoint at 47.42, 9.37 for today"

## How it works

This skill connects to the Geomanic MCP API (`https://geomanic.com/api/v1/mcp`). All data stays on your Geomanic account. The skill only requires network access to `geomanic.com`.

## Requirements

- A free [Geomanic](https://geomanic.com) account
- An MCP API key (generated on the Data page)

## Links

- [Geomanic](https://geomanic.com)
- [GitHub](https://github.com/monswyk/geomanic-mcp-bridge)
- [Monswyk AG](https://www.monswyk.com)
