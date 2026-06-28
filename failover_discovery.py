#!/usr/bin/env python3
"""
failover_discovery.py — xTeVe Failover Discovery System

Two-mode system for channel health monitoring and replacement discovery:
  - Passive Monitor: reads xTeVe logs, learns from actual user usage (zero streams)
  - Sparse Scan: tests failed channels with replacements, one at a time

Zero dependencies (Python 3.10 stdlib only).
Designed to run on the xTeVe host at: /docker_data/xteve-{instance}/config/

Usage (standalone):
    python3 failover_discovery.py /docker_data/xteve-mama/config monitor  # Passive mode
    python3 failover_discovery.py /docker_data/xteve-mama/config scan     # Sparse scan mode
    python3 failover_discovery.py /docker_data/xteve-mama/config --json   # Both modes

Author: Hermes Agent
Date: 2026-06-21
"""

import os
import sys
import re
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any

# Import our modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from health_database import HealthDatabase, SCHEMA_SQL
from m3u_index import M3UIndex


# ─── Configuration Defaults ─────────────────────────────────────────

DEFAULT_CONFIG = {
    'max_concurrent_tests': 1,       # Never exceed provider stream limit
    'test_interval_seconds': 30,     # Wait between tests (provider recovery)
    'max_tests_per_scan': 10,        # Max channels to test per scan run
    'test_timeout_seconds': 3,       # HTTP HEAD timeout per URL test
    'max_failed_channels_to_test': 10,  # Max FAILED channels per scan
    'passive_stream_end_threshold': 5,   # Seconds — if stream ends < this, mark FAILED
    'passive_active_window': 30,     # Seconds — look for "No client" within this window
}


# ─── Name Normalization (for fuzzy matching) ────────────────────────

def normalize_name(name: str) -> str:
    """Normalize channel names for comparison.

    Removes provider prefixes, resolution tags, normalizes whitespace.
    """
    name = re.sub(r'^ECU-', '', name)       # "ECU-" prefix
    name = re.sub(r'^USA\s*[|-]\s*', '', name)  # "USA |" or "USA -"
    name = re.sub(r'\s*\([^)]*\)', '', name)  # Remove "(e)(1080)" etc.
    name = re.sub(r'\s+', ' ', name).strip().lower()  # Normalize whitespace
    return name


def normalize_for_group_search(failed_name: str) -> List[str]:
    """Extract key words from a channel name for group matching."""
    normalized = normalize_name(failed_name)
    return set(normalized.split())


# ─── URL Testing (HTTP HEAD, lightweight) ──────────────────────────

def test_url(url: str, timeout: int = 3) -> Dict[str, Any]:
    """Test a single streaming URL using HTTP HEAD.

    Args:
        url: The M3U8 or stream URL to test
        timeout: Seconds before timing out

    Returns:
        Dict with keys: working (bool), status_code, response_time_ms, error_message
    """
    start = time.time()

    try:
        req = urllib.request.Request(url, method='HEAD')
        # Add common headers that some providers expect
        req.add_header('User-Agent', 'Mozilla/5.0 (xTeVe-Failover-Test)')
        req.add_header('Accept', '*/*')

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed_ms = int((time.time() - start) * 1000)
            return {
                'working': True,
                'status_code': resp.status,
                'response_time_ms': elapsed_ms,
                'error_message': None,
            }

    except urllib.error.HTTPError as e:
        elapsed_ms = int((time.time() - start) * 1000)
        return {
            'working': False,
            'status_code': e.code,
            'response_time_ms': elapsed_ms,
            'error_message': f'HTTP {e.code}: {e.reason}',
        }

    except urllib.error.URLError as e:
        elapsed_ms = int((time.time() - start) * 1000)
        return {
            'working': False,
            'status_code': None,
            'response_time_ms': elapsed_ms,
            'error_message': f'URL Error: {str(e.reason)[:100]}',
        }

    except Exception as e:
        elapsed_ms = int((time.time() - start) * 1000)
        return {
            'working': False,
            'status_code': None,
            'response_time_ms': elapsed_ms,
            'error_message': f'Exception: {str(e)[:100]}',
        }


# ─── Passive Monitor — Read xTeVe Logs ──────────────────────────────

# Patterns to extract from xTeVe logs
TUNER_PATTERN = re.compile(r'Playlist: .* - Tuner: (\d+)/(\d+)')
CHANNEL_PATTERN = re.compile(r'Channel Name:\s*(.+?)\s*$')
STREAMING_ENDED_PATTERN = re.compile(r'No client is using this channel anymore')
ERROR_PATTERN = re.compile(r'(?:ERROR|error):(.+?)(?:\s*$|\s*\[)')
TIMEOUT_PATTERN = re.compile(r'(?:timeout|Timeout)(.*?)(?:\s*$|\s*\[)')
CONNECTION_PATTERN = re.compile(r'(?:Connection|connection).+?(error|failed|refused)(.*)', re.IGNORECASE)


def parse_xteve_log(log_path: str, db: HealthDatabase):
    """Parse xTeVe log file and update channel health based on actual usage.

    Reads the log sequentially, correlating "Channel Name" entries with
    subsequent stream end events to determine if channels worked or failed.

    Args:
        log_path: Path to xteve.log file
        db: HealthDatabase instance for storing results
    # Normalize config_dir: if inside a subdirectory (e.g. config/monitor),
    # resolve to the instance's config directory
    if "/config/monitor" in config_dir:
        config_dir = os.path.dirname(config_dir)
    elif "/config" in config_dir and not any(x in config_dir for x in ["xteve-mama", "xteve-rm", "xteve-oldr"]):
        config_dir = os.path.dirname(config_dir)

    """
    if not os.path.exists(log_path):
        print(f"Log file not found: {log_path}", file=sys.stderr)
        return

    # State machine: track channels being watched
    active_channels: Dict[str, float] = {}  # channel_name -> log timestamp (epoch)
    channels_seen: Dict[str, str] = {}  # channel_name -> x_channel_id (if matchable)

    with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            # Extract channel name
            ch_match = CHANNEL_PATTERN.search(line)
            if ch_match:
                channel_name = ch_match.group(1).strip()
                active_channels[channel_name] = time.time()

                # Try to extract channel ID from log line (if present)
                id_match = re.search(r'x-channelID[:\s]+(\d+)', line)
                if id_match:
                    channels_seen[channel_name] = id_match.group(1)

            # Check for stream ended (successful watch)
            if STREAMING_ENDED_PATTERN.search(line):
                # Find the most recent active channel
                for ch_name in list(active_channels.keys()):
                    elapsed = time.time() - active_channels[ch_name]

                    # Record passive event
                    db.record_passive_event(
                        timestamp=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
                        channel_name=ch_name,
                        event_type='stream_ended',
                        channel_id=channels_seen.get(ch_name),
                        duration_seconds=elapsed,
                    )

                    # If stream lasted long enough, mark as WORKING
                    if elapsed > DEFAULT_CONFIG['passive_stream_end_threshold']:
                        ch_id = channels_seen.get(ch_name, ch_name)  # Use name if no ID
                        db.update_channel_status(
                            channel_id=ch_id,
                            name=ch_name,
                            group_title=None,  # Will be filled from xepg.json if available
                            status='WORKING',
                            source='passive'
                        )
                    else:
                        # Stream ended too quickly — likely failed
                        ch_id = channels_seen.get(ch_name, ch_name)
                        db.update_channel_status(
                            channel_id=ch_id,
                            name=ch_name,
                            group_title=None,
                            status='FAILED',
                            error_message=f'Stream ended after {elapsed:.0f}s (too short)',
                            source='passive'
                        )

                    # Clear this channel from active tracking
                    del active_channels[ch_name]

            # Check for errors/timeouts
            error_match = ERROR_PATTERN.search(line) or TIMEOUT_PATTERN.search(line)
            if error_match:
                for ch_name in list(active_channels.keys()):
                    # Log this as a passive error event
                    db.record_passive_event(
                        timestamp=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
                        channel_name=ch_name,
                        event_type='error',
                        channel_id=channels_seen.get(ch_name),
                        error_message=error_match.group(1).strip()[:200] if error_match else None,
                    )

                    # Mark as FAILED
                    ch_id = channels_seen.get(ch_name, ch_name)
                    db.increment_failure(ch_id)


# ─── Sparse Scan — Test Failed Channels with Replacements ──────────

def find_replacements_in_m3u(failed_channel: Dict[str, Any], m3u_index: M3UIndex) -> List[Dict[str, Any]]:
    """Find working replacements for a failed channel from the M3U index.

    Matching priority:
      1. Exact name match (normalized) — quality 1.0
      2. Same group + shared words — quality based on word overlap
      3. Same provider URL pattern (similar ID) — quality 0.6
      4. Same group, any name (fallback) — quality 0.3

    Args:
        failed_channel: Dict with keys: name, group_title, x_channel_id (from channel_health)
        m3u_index: M3UIndex instance with parsed channels

    Returns:
        List of candidate replacements sorted by match quality (best first)
    """
    failed_name = normalize_name(failed_channel['name'])
    failed_group = failed_channel.get('group_title', '') or ''

    candidates: List[Tuple[M3UChannel, float, str]] = []
    failed_words = normalize_for_group_search(failed_channel['name'])

    # Rank 1: Exact name match (normalized) — any group
    normalized_matches = m3u_index.find_by_name(failed_name)
    for ch in normalized_matches:
        if normalize_name(ch.name) == failed_name and ch.url:
            candidates.append((ch, 1.0, 'exact name match'))

    # Rank 2: Same group + shared words (high priority)
    if failed_group and failed_words:
        group_channels = m3u_index.find_by_group(failed_group)
        for ch in group_channels:
            if not ch.url:
                continue
            m3u_words = normalize_for_group_search(ch.name)
            shared = failed_words & m3u_words  # Intersection

            if len(shared) >= 2:  # At least 2 shared words
                quality = len(shared) / max(len(failed_words), len(m3u_words))
                candidates.append((ch, round(quality, 2), 'same group + shared words'))

    # Rank 3: Same group, any name (fallback)
    if failed_group:
        group_channels = m3u_index.find_by_group(failed_group)
        for ch in group_channels:
            if not ch.url:
                continue
            # Only add if not already covered by higher-ranked match
            existing_names = {c[0].name for c in candidates}
            if ch.name not in existing_names:
                candidates.append((ch, 0.3, 'same group only'))

    # Sort by quality (best first), then by name
    candidates.sort(key=lambda c: (-c[1], c[0].name))

    return candidates


def run_sparse_scan(config_dir: str, db: HealthDatabase) -> Dict[str, Any]:
    """Run a sparse scan of failed channels with replacement discovery.

    Tests one channel at a time, 30 seconds apart, never exceeding provider
    stream limits. For each failed channel, searches the M3U index for
    working alternatives and tests them sequentially.

    Args:
        config_dir: Path to xTeVe config directory
        db: HealthDatabase instance

    Returns:
        JSON-serializable report dict with scan results
    """
    start_time = time.time()

    # Step 1: Get all FAILED channels from the database
    failed_channels = db.get_failed_channels(limit=DEFAULT_CONFIG['max_failed_channels_to_test'])

    if not failed_channels:
        return {
            'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S'),
            'instance': os.path.basename(config_dir).replace('xteve-', ''),
            'summary': {
                'total_channels_in_xepg': 0,
                'channels_marked_failed_by_passive_monitor': 0,
                'replacements_found_in_this_scan': 0,
                'scan_duration_seconds': 0,
                'tests_performed': 0,
            },
            'failed_channels': [],
        }

    # Step 2: Build M3U index (load once, use for all lookups)
    m3u_index = M3UIndex()

    # Auto-discover M3U files in config directory
    m3u_count = 0
    for root, dirs, files in os.walk(config_dir):
        for f in files:
            if f.endswith('.m3u') or f.endswith('.m3u8'):
                m3u_path = os.path.join(root, f)
                count = m3u_index.parse_m3u(m3u_path)
                if count > 0:
                    m3u_count += 1

    print(f"M3U Index loaded: {m3u_index.get_stats()['total_channels']} channels, "
          f"{m3u_count} file(s)")

    # Step 3: Load channel info from xepg.json for group titles
    xepg_path = os.path.join(config_dir, 'xepg.json')
    channel_group_map: Dict[str, str] = {}  # x_channel_id -> group_title

    if os.path.exists(xepg_path):
        try:
            with open(xepg_path, 'r') as f:
                xepg = json.load(f)

            for ch_id, ch_data in xepg.items():
                if isinstance(ch_data, dict):
                    # Try to extract group_title from x-group-title or tvg-group
                    group = ch_data.get('x-group-title') or ch_data.get('tvg-group', '')
                    if group:
                        channel_group_map[ch_id] = group

                    # Also map by name for fallback lookup
                    ch_name = ch_data.get('x-name', '') or ''
                    if ch_name:
                        # Map name to group for channels not in M3U index
                        channel_group_map[ch_name] = group

        except (json.JSONDecodeError, KeyError) as e:
            print(f"Warning: Could not parse xepg.json: {e}", file=sys.stderr)

    # Step 4: Test each failed channel and find replacements
    total_tests = 0
    replacements_found = 0
    scan_results: List[Dict[str, Any]] = []

    for failed_ch in failed_channels[:DEFAULT_CONFIG['max_failed_channels_to_test']]:
        channel_id = failed_ch.get('x_channel_id', failed_ch['name'])
        channel_name = failed_ch['name']
        group_title = (failed_ch.get('group_title') or channel_group_map.get(channel_id, '')
                       or channel_group_map.get(channel_name, ''))

        print(f"\nTesting failed: {channel_name} (ID: {channel_id})")

        # Step 4a: Test the original channel's direct URL (from urls.json if available)
        urls_path = os.path.join(config_dir, 'urls.json')
        direct_url = None

        if os.path.exists(urls_path):
            try:
                with open(urls_path, 'r') as f:
                    urls_data = json.load(f)

                # Try to find direct URL by channel ID or name
                for url_entry in urls_data:
                    if isinstance(url_entry, dict):
                        entry_id = str(url_entry.get('channelNumber', url_entry.get('id', '')))
                        if entry_id == str(channel_id):
                            direct_url = url_entry.get('url') or url_entry.get('URL', '')
                            break
                        # Also try by name match
                        if url_entry.get('x-name', '') == channel_name:
                            direct_url = url_entry.get('url') or url_entry.get('URL', '')
                            break

            except (json.JSONDecodeError, KeyError) as e:
                print(f"Warning: Could not parse urls.json: {e}", file=sys.stderr)

        # Step 4b: Search M3U for matching working alternatives
        candidates = find_replacements_in_m3u(failed_ch, m3u_index)

        # Step 4c: Test candidates (one at a time, 30 seconds apart)
        replacements_found_list = []
        replacements_none_list = []

        # Test the direct URL first (if found)
        if direct_url:
            result = test_url(direct_url, timeout=DEFAULT_CONFIG['test_timeout_seconds'])
            total_tests += 1

        # Test M3U candidates (sparse — one at a time, 30s apart)
        for candidate_ch, quality, match_reason in candidates[:5]:  # Top 5 matches
            result = test_url(candidate_ch.url, timeout=DEFAULT_CONFIG['test_timeout_seconds'])
            total_tests += 1

            if result['working']:
                replacements_found += 1

                # Save to database
                db.add_replacement(
                    failed_channel_id=channel_id,
                    replacement_url=candidate_ch.url,
                    match_quality=quality,
                    match_reason=match_reason
                )

                replacements_found_list.append({
                    'name': candidate_ch.name,
                    'group': candidate_ch.group_title or group_title,
                    'url': candidate_ch.url,
                    'match_quality': quality,
                    'match_reason': match_reason,
                    'tested_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S'),
                    'test_result': f"WORKING (HTTP {result['status_code']}, "
                                   f"{result['response_time_ms']}ms)",
                })

            else:
                replacements_none_list.append({
                    'name': candidate_ch.name,
                    'group': candidate_ch.group_title or group_title,
                    'reason': f"Test failed: {result.get('error_message', 'Unknown')}",
                })

            # Wait between tests (but not after the last one)
            if candidate_ch != candidates[-1]:
                time.sleep(DEFAULT_CONFIG['test_interval_seconds'])

        scan_results.append({
            'channel_id': channel_id,
            'name': channel_name,
            'group': group_title,
            'passive_status': failed_ch.get('last_status', 'FAILED'),
            'failure_count': failed_ch.get('failure_count', 0),
            'replacements_found': replacements_found_list,
            'replacements_none': replacements_none_list if not replacements_found_list else [],
        })

    scan_duration = time.time() - start_time

    # Step 5: Build group health summary
    groups = db.get_group_health()

    return {
        'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S'),
        'instance': os.path.basename(config_dir).replace('xteve-', ''),
        'summary': {
            'total_channels_in_xepg': len(failed_channels) + sum(
                s.get('summary', {}).get('WORKING', 0) for s in [db.generate_report()]
            ),
            'channels_marked_failed_by_passive_monitor': len(failed_channels),
            'replacements_found_in_this_scan': replacements_found,
            'scan_duration_seconds': round(scan_duration, 1),
            'tests_performed': total_tests,
        },
        'failed_channels': scan_results,
        'group_health': {g: counts for g, counts in groups.items()},
    }


# ─── Tuner Awareness (Optional) ─────────────────────────────────────

def should_test_tuners(config_dir: str) -> bool:
    """Whether tuners are available for testing.

    No tuner backend is integrated, so scanning always proceeds.
    """
    return True


# ─── Main Entry Point ──────────────────────────────────────────────

def run_monitor(config_dir: str, db: HealthDatabase):
    """Run passive monitoring mode — read xTeVe logs, update health.

    This is the "zero streams consumed" mode that learns from actual
    user usage patterns by parsing xTeVe log files.

    Args:
        config_dir: Path to xTeVe config directory
        db: HealthDatabase instance
    """
    # Normalize: if config_dir is inside a subdirectory, resolve to instance config dir
    real_config = os.path.dirname(config_dir) if "/config/monitor" in config_dir else config_dir
    # Also handle when run from /docker_data/xteve-*/config (basename is "config")
    if os.path.basename(config_dir) == "config":
        real_config = config_dir
    # Find xteve.log — try common locations
    # Normalize config_dir: if inside a subdirectory (e.g. config/monitor), resolve to instance config dir
    if "/config/monitor" in config_dir:
        config_dir = os.path.dirname(config_dir)
    log_path = None

    # Try: config/log/xteve.log (as per design doc)
    candidate = os.path.join(config_dir, 'log', 'xteve.log')
    if os.path.exists(candidate):
        log_path = candidate

    # Try: config/xteve.log (alternate location)
    if not log_path and os.path.exists(os.path.join(config_dir, 'xteve.log')):
        log_path = os.path.join(config_dir, 'xteve.log')

    # Try: /docker_data/xteve-{instance}/log/xteve.log (as per spec)
    if not log_path:
        instance_name = os.path.basename(os.path.dirname(config_dir)).replace('xteve-', '')
        candidate = f'/docker_data/xteve-{instance_name}/log/xteve.log'
        if os.path.exists(candidate):
            log_path = candidate

    if not log_path:
        print(f"Warning: Could not find xteve.log in {config_dir}", file=sys.stderr)
        print(f"  Expected locations tried:", file=sys.stderr)
        print(f"    {os.path.join(config_dir, 'log', 'xteve.log')}", file=sys.stderr)
        print(f"    {os.path.join(config_dir, 'xteve.log')}", file=sys.stderr)
        return

    print(f"Passive monitor reading: {log_path}")

    # Parse log and update health database
    parse_xteve_log(log_path, db)

    # Report what was found
    summary = db.get_channel_health_summary()
    print(f"Passive monitor complete:")
    print(f"  Working: {summary['WORKING']} | Failed: {summary['FAILED']} | Unknown: {summary['UNKNOWN']}")


def run_scan(config_dir: str, db: HealthDatabase) -> Dict[str, Any]:
    """Run sparse scan mode — test failed channels with replacements.

    This is the "sparse testing" mode that respects provider stream limits:
    - Tests one channel at a time (sequential)
    - 30 seconds between tests (configurable)
    - Max 10 channels per scan run

    Args:
        config_dir: Path to xTeVe config directory
        db: HealthDatabase instance

    Returns:
        JSON-serializable report dict
    """
    # Check tuners before testing (optional — non-blocking)
    tuner_available = should_test_tuners(config_dir)

    if not tuner_available:
        print("Warning: Too many tuners in use — skipping scan to avoid blocking active streams")

    print(f"Sparse scan starting... (tuners available: {tuner_available})")

    # Run the sparse scan
    report = run_sparse_scan(config_dir, db)

    print(f"Sparse scan complete:")
    print(f"  Tests performed: {report['summary']['tests_performed']}")
    print(f"  Replacements found: {report['summary']['replacements_found_in_this_scan']}")
    print(f"  Duration: {report['summary']['scan_duration_seconds']}s")

    return report


def run_full(config_dir: str, db: HealthDatabase) -> Dict[str, Any]:
    """Run both passive monitor and sparse scan in sequence.

    1. Passive monitor reads xTeVe logs (zero streams consumed)
    2. Sparse scan tests failed channels with replacements

    Args:
        config_dir: Path to xTeVe config directory
        db: HealthDatabase instance

    Returns:
        Combined JSON-serializable report dict (sparse scan results)
    """
    # Phase 1: Passive monitor
    run_monitor(config_dir, db)

    # Phase 2: Sparse scan (only if there are failed channels)
    summary = db.get_channel_health_summary()

    if summary['FAILED'] == 0:
        print("No failed channels to test — scan skipped")
        return {
            'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S'),
            'instance': os.path.basename(config_dir).replace('xteve-', ''),
            'summary': {
                'total_channels_in_xepg': 0,
                'channels_marked_failed_by_passive_monitor': 0,
                'replacements_found_in_this_scan': 0,
                'scan_duration_seconds': 0,
                'tests_performed': 0,
            },
            'failed_channels': [],
        }

    return run_scan(config_dir, db)


def main():
    """CLI entry point for failover_discovery.py."""
    import argparse

    parser = argparse.ArgumentParser(
        description='xTeVe Failover Discovery System — Passive Monitor + Sparse Scan',
        epilog="""Modes:
  monitor  — Read xTeVe logs, learn from actual usage (zero streams consumed)
  scan     — Test failed channels with replacements (sparse, sequential)
  full     — Run both monitor + scan (default when no mode specified)

Examples:
  python3 failover_discovery.py /docker_data/xteve-mama/config monitor
  python3 failover_discovery.py /docker_data/xteve-mama/config scan --json
  python3 failover_discovery.py /docker_data/xteve-mama/config
"""
    )

    parser.add_argument('config_dir', help='Path to xTeVe config directory')
    parser.add_argument('mode', nargs='?', default='full',
                        choices=['monitor', 'scan', 'full'],
                        help='Operation mode (default: full)')
    parser.add_argument('--json', action='store_true', help='Output results as JSON (for cron)')
    parser.add_argument('--m3u', metavar='FILE', help='Parse specific M3U file (debug)')

    args = parser.parse_args()
    config_dir = os.path.abspath(args.config_dir)

    # Initialize database (creates health.db if needed)
    db = HealthDatabase(config_dir)

    try:
        # Optional: parse M3U for debugging
        if args.m3u:
            m3u_path = os.path.join(config_dir, args.m3u) if not os.path.isabs(args.m3u) else args.m3u
            index = M3UIndex()
            count = index.parse_m3u(m3u_path)
            print(f"M3U parsed: {count} channels from {args.m3u}")

        # Run the selected mode
        if args.mode == 'monitor':
            run_monitor(config_dir, db)

        elif args.mode == 'scan':
            report = run_scan(config_dir, db)

        else:  # full (default)
            report = run_full(config_dir, db)

        # Output results
        if args.json:
            print(json.dumps(report, indent=2))

    finally:
        db.close()


if __name__ == '__main__':
    main()
