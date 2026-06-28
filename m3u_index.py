#!/usr/bin/env python3
"""
m3u_index.py — M3U playlist parser and searchable index

Parses full M3U playlists into structured data for fast lookup by:
  - Channel name (normalized)
  - Group title
  - TVG ID (provider internal ID)

Zero dependencies (Python 3.10 stdlib only).
Designed to run on the xTeVe host at: /docker_data/xteve-{instance}/config/

Usage (standalone):
    python3 m3u_index.py /docker_data/xteve-mama/config --parse "data/MVMOIDJ3OTTEPQWN8Z4Y.m3u"
    python3 m3u_index.py /docker_data/xteve-mama/config --index  (auto-discover M3U files)

Author: Hermes Agent
Date: 2026-06-21
"""

import os
import re
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Tuple, Any


class M3UChannel:
    """Represents a single channel from an M3U playlist."""

    def __init__(self, name: str = '', group_title: str = '',
                 tvg_id: str = '', tvg_logo: str = '', url: str = '',
                 source_m3u: str = ''):
        self.name = name
        self.group_title = group_title or ''
        self.tvg_id = tvg_id or ''
        self.tvg_logo = tvg_logo or ''
        self.url = url
        self.source_m3u = source_m3u

    def __repr__(self):
        return f"M3UChannel(name='{self.name}', group='{self.group_title or 'N/A'}', url='{self.url[:60]}...')"


class M3UIndex:
    """Parse and index M3U playlists for fast lookup."""

    def __init__(self):
        self.channels: List[M3UChannel] = []
        self.by_name: Dict[str, List[M3UChannel]] = defaultdict(list)
        self.by_group: Dict[str, List[M3UChannel]] = defaultdict(list)
        self.by_tvg_id: Dict[str, M3UChannel] = {}

    def parse_m3u(self, m3u_path: str) -> int:
        """
        Parse a single M3U file and build the index.

        Args:
            m3u_path: Path to the .m3u file

        Returns:
            Number of channels parsed
        """
        if not os.path.exists(m3u_path):
            print(f"Warning: M3U file not found: {m3u_path}", file=sys.stderr)
            return 0

        channels = self._parse_m3u_file(m3u_path)
        source_name = os.path.basename(m3u_path)

        for ch in channels:
            ch.source_m3u = source_name
            self.channels.append(ch)

        self._build_indexes()
        return len(channels)

    def _parse_m3u_file(self, m3u_path: str) -> List[M3UChannel]:
        """Parse M3U content into M3UChannel objects."""
        channels = []

        with open(m3u_path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()

        current_channel: Optional[M3UChannel] = None
        i = 0

        while i < len(lines):
            line = lines[i].strip()

            # M3U header
            if line.startswith('#EXTM3U'):
                i += 1
                continue

            # EXTINF line (channel metadata)
            if line.startswith('#EXTINF:'):
                current_channel = M3UChannel()

                # Parse group-title
                group_match = re.search(r'group-title="([^"]*)"', line)
                if group_match:
                    current_channel.group_title = group_match.group(1).strip()

                # Parse tvg-id
                id_match = re.search(r'tvg-id="([^"]*)"', line)
                if id_match:
                    current_channel.tvg_id = id_match.group(1).strip()

                # Parse tvg-logo
                logo_match = re.search(r'tvg-logo="([^"]*)"', line)
                if logo_match:
                    current_channel.tvg_logo = logo_match.group(1).strip()

                # Channel name is after the last comma
                comma_idx = line.rfind(',')
                if comma_idx > 0:
                    current_channel.name = line[comma_idx + 1:].strip()

                i += 1
                continue

            # Stream URL (non-empty, non-comment line after EXTINF)
            if current_channel is not None and line and not line.startswith('#'):
                current_channel.url = line.strip()

            # End of this channel's entry
            if (current_channel is not None and current_channel.url and
                    (i + 1 >= len(lines) or lines[i + 1].startswith('#'))):
                channels.append(current_channel)
                current_channel = None

            i += 1

        return channels

    def _build_indexes(self):
        """Build lookup indexes from parsed channels."""
        self.by_name.clear()
        self.by_group.clear()
        self.by_tvg_id.clear()

        for ch in self.channels:
            if ch.name:
                self.by_name[ch.name.lower()].append(ch)

            if ch.group_title:
                self.by_group[ch.group_title].append(ch)

            if ch.tvg_id:
                # Keep first occurrence (in case of duplicates)
                if ch.tvg_id not in self.by_tvg_id:
                    self.by_tvg_id[ch.tvg_id] = ch

    def find_by_name(self, name: str) -> List[M3UChannel]:
        """Find channels by (case-insensitive) name."""
        return self.by_name.get(name.lower(), [])

    def find_by_group(self, group: str) -> List[M3UChannel]:
        """Find channels by group title."""
        return self.by_group.get(group, [])

    def find_by_tvg_id(self, tvg_id: str) -> Optional[M3UChannel]:
        """Find a single channel by TVG ID."""
        return self.by_tvg_id.get(tvg_id)

    def find_all(self) -> List[M3UChannel]:
        """Get all indexed channels."""
        return list(self.channels)

    def get_stats(self) -> Dict[str, Any]:
        """Get index statistics."""
        return {
            'total_channels': len(self.channels),
            'unique_names': len(set(ch.name.lower() for ch in self.channels if ch.name)),
            'unique_groups': len(self.by_group),
            'groups': sorted(self.by_group.keys()),
            'unique_tvg_ids': len(self.by_tvg_id),
        }

    def get_group_summary(self) -> Dict[str, int]:
        """Get channel count per group."""
        summary = {}
        for group, chs in self.by_group.items():
            summary[group] = len(chs)
        return dict(sorted(summary.items(), key=lambda x: x[1], reverse=True))

    def __len__(self):
        return len(self.channels)

    def __repr__(self):
        stats = self.get_stats()
        return (f"M3UIndex(channels={stats['total_channels']}, "
                f"groups={stats['unique_groups']}, tvg_ids={stats['unique_tvg_ids']})")


# ─── Standalone CLI ─────────────────────────────────────────────────

def main():
    """CLI entry point for m3u_index.py."""
    import argparse

    parser = argparse.ArgumentParser(description='M3U Playlist Parser and Index')
    parser.add_argument('config_dir', help='Path to xTeVe config directory')
    parser.add_argument('--parse', metavar='FILE', help='Parse a specific M3U file')
    parser.add_argument('--index', action='store_true', help='Auto-discover and index all M3U files')
    parser.add_argument('--stats', action='store_true', help='Show index statistics')
    parser.add_argument('--search-name', metavar='NAME', help='Search channels by name')
    parser.add_argument('--search-group', metavar='GROUP', help='Search channels by group')

    args = parser.parse_args()
    config_dir = os.path.abspath(args.config_dir)

    index = M3UIndex()
    m3u_files_found = 0

    try:
        if args.parse:
            # Parse specific file
            m3u_path = os.path.join(config_dir, args.parse) if not os.path.isabs(args.parse) else args.parse
            count = index.parse_m3u(m3u_path)
            print(f"Parsed {count} channels from: {m3u_path}")

        elif args.index:
            # Auto-discover M3U files in config directory
            for root, dirs, files in os.walk(config_dir):
                for f in files:
                    if f.endswith('.m3u') or f.endswith('.m3u8'):
                        m3u_path = os.path.join(root, f)
                        count = index.parse_m3u(m3u_path)
                        m3u_files_found += 1
                        print(f"Indexed: {f} ({count} channels)")

            if m3u_files_found == 0:
                print(f"No M3U files found in {config_dir}", file=sys.stderr)

        else:
            # Auto-discover by default (same as --index for convenience)
            for root, dirs, files in os.walk(config_dir):
                for f in files:
                    if f.endswith('.m3u') or f.endswith('.m3u8'):
                        m3u_path = os.path.join(root, f)
                        count = index.parse_m3u(m3u_path)
                        m3u_files_found += 1

        # Output based on requested action
        if args.stats:
            stats = index.get_stats()
            print(f"\nIndex Statistics:")
            print(f"  Total channels: {stats['total_channels']}")
            print(f"  Unique names:   {stats['unique_names']}")
            print(f"  Groups:         {stats['unique_groups']}")
            print(f"  TVG IDs:        {stats['unique_tvg_ids']}")

            summary = index.get_group_summary()
            if summary:
                print(f"\nGroups ({len(summary)}):")
                for g, c in list(summary.items())[:20]:  # Top 20
                    print(f"  {g}: {c} channels")

        elif args.search_name:
            results = index.find_by_name(args.search_name)
            if results:
                print(f"Found {len(results)} channel(s) matching '{args.search_name}':")
                for ch in results:
                    print(f"  {ch.name}")
                    print(f"    Group: {ch.group_title or 'N/A'}")
                    print(f"    URL:   {ch.url[:80]}...")
            else:
                print(f"No channels found matching '{args.search_name}'")

        elif args.search_group:
            results = index.find_by_group(args.search_group)
            if results:
                print(f"Found {len(results)} channel(s) in group '{args.search_group}':")
                for ch in results[:20]:  # Limit output
                    print(f"  {ch.name}")
            else:
                print(f"No channels found in group '{args.search_group}'")

        else:
            # Default: basic summary
            stats = index.get_stats()
            print(f"M3U Index: {stats['total_channels']} channels, "
                  f"{stats['unique_groups']} groups, {m3u_files_found} file(s)")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
