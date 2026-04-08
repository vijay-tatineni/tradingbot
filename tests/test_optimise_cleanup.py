"""
tests/test_optimise_cleanup.py — Test that old optimise jobs are cleaned up.
"""

import time

import pytest


def test_old_jobs_cleaned_up():
    """Jobs older than 24 hours should be removed on cleanup."""
    import api_server

    api_server._optimise_jobs.clear()
    api_server._optimise_jobs['old_job'] = {
        'symbol': 'TEST', 'status': 'complete', 'progress': 100,
        '_created_at': time.time() - 90000,  # 25 hours ago
    }
    api_server._optimise_jobs['recent_job'] = {
        'symbol': 'TEST2', 'status': 'running', 'progress': 50,
        '_created_at': time.time(),
    }

    api_server._cleanup_old_jobs()

    assert 'old_job' not in api_server._optimise_jobs
    assert 'recent_job' in api_server._optimise_jobs


def test_recent_jobs_kept():
    """Jobs within 24 hours should not be removed."""
    import api_server

    api_server._optimise_jobs.clear()
    api_server._optimise_jobs['new_job'] = {
        'symbol': 'TEST', 'status': 'complete', 'progress': 100,
        '_created_at': time.time() - 3600,  # 1 hour ago
    }

    api_server._cleanup_old_jobs()

    assert 'new_job' in api_server._optimise_jobs


def test_cleanup_handles_missing_created_at():
    """Jobs without _created_at are treated as old and removed."""
    import api_server

    api_server._optimise_jobs.clear()
    api_server._optimise_jobs['no_ts_job'] = {
        'symbol': 'TEST', 'status': 'complete', 'progress': 100,
        # no _created_at key
    }

    api_server._cleanup_old_jobs()

    assert 'no_ts_job' not in api_server._optimise_jobs
