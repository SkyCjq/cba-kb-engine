"""User scope decisions for current processing; historical evidence is retained."""

# Stable IDs deliberately survive file renames, moves, and stale proposals.
EXCLUDED_DRIVE_IDS = frozenset({
    '1D72_i0dzR9TmeV_XH8KeBr_BTxxwFPIM',
    '1WYVqi94bqVGOLsD7t1nmMXyTMTU4TOlr',
})


def excluded_from_current(source):
    """Accept discovery items and registry rows, including source-ID-only rows."""
    return any(str(source.get(key) or '').removeprefix('drive:') in EXCLUDED_DRIVE_IDS
               for key in ('id', 'drive_file_id', 'source_id'))
