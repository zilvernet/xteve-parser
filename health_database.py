#!/usr/bin/env python3
"""
health_database.py — SQLite wrapper for xTeVe channel health tracking

Zero dependencies (Python 3.10 stdlib sqlite3 module).
File-based storage on the xTeVe host — no server needed.

Designed to run on: /docker_data/xteve-{instance}/config/
Can be imported by failover_discovery.py or used standalone.

Usage (standalone):
    python3 health_database.py /docker_data/xteve-mama/config --init
    python3 health_database.py /docker_data/xteve-mama/config --report

Author: Hermes Agent
Date: 2026-06-21
"""

import os
import sys
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any


# ─── Database Schema ────────────────────────────────────────────────

SCHEMA_SQL = """
-- Per-channel health tracking
CREATE TABLE IF NOT EXISTS channel_health (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    x_channel_id TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    group_title TEXT,

    last_tested TIMESTAMP,
    last_status TEXT DEFAULT 'UNKNOWN',
    failure_count INTEGER DEFAULT 0,

    response_time_ms INTEGER,
    error_message TEXT,
    source TEXT DEFAULT 'passive',

    active BOOLEAN DEFAULT TRUE,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- M3U channel index (built once from full playlist)
CREATE TABLE IF NOT EXISTS m3u_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    group_title TEXT,
    tvg_id TEXT,
    url TEXT NOT NULL,
    source_m3u TEXT,

    UNIQUE(name, url)
);

-- Replacement relationships (failed -> working alternatives)
CREATE TABLE IF NOT EXISTS replacements (
    failed_channel_id TEXT NOT NULL,
    replacement_url TEXT NOT NULL,
    match_quality REAL,
    match_reason TEXT,
    tested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    active BOOLEAN DEFAULT TRUE,

    PRIMARY KEY (failed_channel_id, replacement_url),
    FOREIGN KEY (failed_channel_id) REFERENCES channel_health(x_channel_id) ON DELETE CASCADE
);

-- Per-group health snapshots (for trend detection)
CREATE TABLE IF NOT EXISTS group_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_name TEXT NOT NULL,
    snapshot_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    total_channels INTEGER,
    working_count INTEGER,
    failed_count INTEGER,
    unknown_count INTEGER
);

-- Passive monitor log (from xTeVe logs)
CREATE TABLE IF NOT EXISTS passive_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP,
    channel_name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    channel_id TEXT,
    duration_seconds REAL,
    error_message TEXT
);

-- Indexes for fast lookups
CREATE INDEX IF NOT EXISTS idx_channel_health_name ON channel_health(name);
CREATE INDEX IF NOT EXISTS idx_channel_health_status ON channel_health(last_status);
CREATE INDEX IF NOT EXISTS idx_m3u_channels_name ON m3u_channels(name);
CREATE INDEX IF NOT EXISTS idx_m3u_channels_group ON m3u_channels(group_title);
CREATE INDEX IF NOT EXISTS idx_replacements_failed ON replacements(failed_channel_id);
"""


class HealthDatabase:
    """SQLite wrapper for xTeVe channel health data."""

    def __init__(self, config_dir: str):
        """
        Initialize database connection.

        Args:
            config_dir: Path to xTeVe config directory (e.g., /docker_data/xteve-mama/config)
        """
        self.config_dir = os.path.abspath(config_dir)
        self.db_path = os.path.join(self.config_dir, 'health.db')
        self.conn: Optional[sqlite3.Connection] = None

    def connect(self) -> sqlite3.Connection:
        """Open (or create and initialize) the database."""
        if self.conn is None:
            os.makedirs(self.config_dir, exist_ok=True)
            self.conn = sqlite3.connect(
                self.db_path,
                timeout=10,
                isolation_level=None  # autocommit for DDL
            )
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA foreign_keys=ON")

            # Create all tables if they don't exist
            self.conn.executescript(SCHEMA_SQL)

        return self.conn

    def close(self):
        """Close the database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # ─── Channel Health Queries ──────────────────────────────────────

    def get_channel(self, channel_id: str) -> Optional[Dict[str, Any]]:
        """Get a single channel's health record."""
        conn = self.connect()
        row = conn.execute(
            "SELECT * FROM channel_health WHERE x_channel_id = ?", (channel_id,)
        ).fetchone()
        if row:
            return dict(row)
        return None

    def get_failed_channels(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get all channels marked as FAILED, ordered by failure count desc."""
        conn = self.connect()
        rows = conn.execute(
            "SELECT * FROM channel_health WHERE last_status = 'FAILED' ORDER BY failure_count DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_channels(self, status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all channels, optionally filtered by status."""
        conn = self.connect()
        if status_filter:
            rows = conn.execute(
                "SELECT * FROM channel_health WHERE last_status = ? ORDER BY name",
                (status_filter,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM channel_health ORDER BY name"
            ).fetchall()
        return [dict(r) for r in rows]

    def update_channel_status(
        self, channel_id: str, name: str, group_title: Optional[str],
        status: str, response_time_ms: Optional[int] = None,
        error_message: Optional[str] = None, source: str = 'passive'
    ):
        """Update or insert a channel's health record."""
        conn = self.connect()
        now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

        existing = conn.execute(
            "SELECT id, failure_count FROM channel_health WHERE x_channel_id = ?", (channel_id,)
        ).fetchone()

        if existing:
            old_failure_count = existing[1] or 0
            new_failure_count = old_failure_count + (0 if status == 'WORKING' else 1)

            conn.execute(
                """UPDATE channel_health SET
                    name = ?, group_title = ?, last_tested = ?, last_status = ?,
                    failure_count = ?, response_time_ms = ?, error_message = ?,
                    source = ?, updated_at = ?
                   WHERE x_channel_id = ?""",
                (name, group_title, now, status, new_failure_count, response_time_ms, error_message, source, channel_id, channel_id)
            )
        else:
            conn.execute(
                """INSERT INTO channel_health (x_channel_id, name, group_title, last_tested,
                    last_status, failure_count, response_time_ms, error_message, source)
                   VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)""",
                (channel_id, name, group_title, now, status, response_time_ms, error_message, source)
            )

    def increment_failure(self, channel_id: str):
        """Increment failure count for a channel (used when passive monitor sees another error)."""
        conn = self.connect()
        existing = conn.execute(
            "SELECT id, failure_count FROM channel_health WHERE x_channel_id = ?", (channel_id,)
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE channel_health SET failure_count = ?, last_status = 'FAILED', updated_at = ? WHERE x_channel_id = ?",
                (existing[1] + 1, datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'), channel_id)
            )
        else:
            conn.execute(
                "INSERT INTO channel_health (x_channel_id, name, last_status, failure_count) VALUES (?, 'Unknown', 'FAILED', 1)",
                (channel_id,)
            )

    def get_channel_health_summary(self) -> Dict[str, int]:
        """Get summary counts by status."""
        conn = self.connect()
        row = conn.execute(
            "SELECT last_status, COUNT(*) FROM channel_health GROUP BY last_status"
        ).fetchall()
        summary = {'WORKING': 0, 'FAILED': 0, 'UNKNOWN': 0}
        for r in row:
            if r[0] in summary:
                summary[r[0]] = r[1]
        return summary

    def get_group_health(self) -> Dict[str, Dict[str, int]]:
        """Get per-group health counts."""
        conn = self.connect()
        rows = conn.execute(
            """SELECT group_title, last_status, COUNT(*)
               FROM channel_health
               WHERE group_title IS NOT NULL AND group_title != ''
               GROUP BY group_title, last_status ORDER BY group_title"""
        ).fetchall()

        groups: Dict[str, Dict[str, int]] = {}
        for r in rows:
            group_name = r[0]
            if group_name not in groups:
                groups[group_name] = {'total': 0, 'WORKING': 0, 'FAILED': 0, 'UNKNOWN': 0}
            groups[group_name][r[1]] = r[2]
            groups[group_name]['total'] += r[2]

        return groups

    def save_group_snapshot(self, group_name: str, total: int, working: int, failed: int, unknown: int):
        """Save a per-group health snapshot for trend detection."""
        conn = self.connect()
        now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        conn.execute(
            """INSERT INTO group_snapshots (group_name, snapshot_time, total_channels,
                working_count, failed_count, unknown_count) VALUES (?, ?, ?, ?, ?, ?)""",
            (group_name, now, total, working, failed, unknown)
        )

    # ─── Passive Events ──────────────────────────────────────────────

    def record_passive_event(
        self, timestamp: str, channel_name: str, event_type: str,
        channel_id: Optional[str] = None, duration_seconds: Optional[float] = None,
        error_message: Optional[str] = None
    ):
        """Record a passive monitoring event from xTeVe logs."""
        conn = self.connect()
        conn.execute(
            """INSERT INTO passive_events (timestamp, channel_name, event_type,
                channel_id, duration_seconds, error_message) VALUES (?, ?, ?, ?, ?, ?)""",
            (timestamp, channel_name, event_type, channel_id, duration_seconds, error_message)
        )

    def get_recent_passive_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent passive events (most recent first)."""
        conn = self.connect()
        rows = conn.execute(
            "SELECT * FROM passive_events ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ─── M3U Channel Index ───────────────────────────────────────────

    def add_m3u_channels(self, channels: List[Dict[str, str]]):
        """
        Bulk-add M3U channels to the index.

        Args:
            channels: List of dicts with keys: name, group_title, tvg_id, url, source_m3u
        """
        conn = self.connect()
        for ch in channels:
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO m3u_channels (name, group_title, tvg_id, url, source_m3u)
                       VALUES (?, ?, ?, ?, ?)""",
                    (ch.get('name', ''), ch.get('group_title'), ch.get('tvg_id'),
                     ch.get('url', ''), ch.get('source_m3u'))
                )
            except sqlite3.IntegrityError:
                pass  # Duplicate — skip silently

    def get_m3u_channel(self, name: str) -> List[Dict[str, Any]]:
        """Find M3U channels by exact name match."""
        conn = self.connect()
        rows = conn.execute(
            "SELECT * FROM m3u_channels WHERE name = ?", (name,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_m3u_channels_by_group(self, group_title: str) -> List[Dict[str, Any]]:
        """Find M3U channels by group title."""
        conn = self.connect()
        rows = conn.execute(
            "SELECT * FROM m3u_channels WHERE group_title = ?", (group_title,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_m3u_channels(self) -> List[Dict[str, Any]]:
        """Get all indexed M3U channels."""
        conn = self.connect()
        rows = conn.execute("SELECT * FROM m3u_channels").fetchall()
        return [dict(r) for r in rows]

    def get_replacements_for_channel(self, channel_id: str) -> List[Dict[str, Any]]:
        """Get all active replacements for a failed channel."""
        conn = self.connect()
        rows = conn.execute(
            """SELECT r.*, ch.name as replacement_name, ch.group_title as replacement_group
               FROM replacements r
               JOIN channel_health ch ON r.replacement_url = ch.x_channel_id OR 1=1
               WHERE r.failed_channel_id = ? AND r.active = 1
               ORDER BY r.match_quality DESC""", (channel_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def add_replacement(self, failed_channel_id: str, replacement_url: str,
                        match_quality: float, match_reason: str):
        """Add a replacement relationship."""
        conn = self.connect()
        try:
            conn.execute(
                """INSERT OR IGNORE INTO replacements (failed_channel_id, replacement_url,
                    match_quality, match_reason) VALUES (?, ?, ?, ?)""",
                (failed_channel_id, replacement_url, match_quality, match_reason)
            )
        except sqlite3.IntegrityError:
            pass  # Already exists

    def get_group_snapshots(self, group_name: str, limit: int = 7) -> List[Dict[str, Any]]:
        """Get recent snapshots for a group (for trend detection)."""
        conn = self.connect()
        rows = conn.execute(
            """SELECT * FROM group_snapshots WHERE group_name = ? ORDER BY snapshot_time DESC LIMIT ?""",
            (group_name, limit)
        ).fetchall()
        return [dict(r) for r in rows]

    # ─── Report Generation ───────────────────────────────────────────

    def generate_report(self) -> Dict[str, Any]:
        """Generate a full health report."""
        summary = self.get_channel_health_summary()
        groups = self.get_group_health()

        return {
            'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'summary': summary,
            'groups': groups,
            'total_channels': sum(summary.values()),
        }

    def __repr__(self):
        return f"HealthDatabase(path='{self.db_path}')"


# ─── Standalone CLI ─────────────────────────────────────────────────

def main():
    """CLI entry point for health_database.py."""
    import argparse

    parser = argparse.ArgumentParser(description='xTeVe Health Database Manager')
    parser.add_argument('config_dir', help='Path to xTeVe config directory')
    parser.add_argument('--init', action='store_true', help='Initialize database schema only')
    parser.add_argument('--report', action='store_true', help='Generate health report (JSON)')
    parser.add_argument('--channels', action='store_true', help='List all channels (compact)')
    parser.add_argument('--failed', action='store_true', help='List failed channels only')

    args = parser.parse_args()

    db = HealthDatabase(args.config_dir)

    try:
        if args.init:
            conn = db.connect()
            print(f"Database initialized at: {db.db_path}")

        elif args.report:
            report = db.generate_report()
            print(json.dumps(report, indent=2))

        elif args.channels:
            channels = db.get_all_channels()
            for ch in channels:
                print(f"  [{ch['last_status']:8s}] {ch['x_channel_id']} - {ch['name']} (group: {ch.get('group_title', 'N/A')})")

        elif args.failed:
            channels = db.get_failed_channels()
            if not channels:
                print("No failed channels found.")
            else:
                for ch in channels:
                    print(f"  {ch['x_channel_id']} - {ch['name']} (failures: {ch['failure_count']}, last: {ch.get('error_message', 'N/A')})")

        else:
            # Default: show summary
            report = db.generate_report()
            print(f"Channel Health Summary (as of {report['timestamp']})")
            print(f"  Total:   {report['summary']['WORKING'] + report['summary']['FAILED'] + report['summary']['UNKNOWN']}")
            print(f"  Working: {report['summary']['WORKING']}")
            print(f"  Failed:  {report['summary']['FAILED']}")
            print(f"  Unknown: {report['summary']['UNKNOWN']}")

    finally:
        db.close()


if __name__ == '__main__':
    import json
    main()
