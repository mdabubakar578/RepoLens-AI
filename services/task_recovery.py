"""
services/task_recovery.py
===========================
Utility to recover stale tasks that were interrupted.
"""
import logging
import database

logger = logging.getLogger("repolens.recovery")

def recover_stale_tasks():
    """
    Scans the database for analyses that have been stuck in 'processing' (or 'pending')
    for more than 10 minutes and marks them as 'error'.
    """
    recovered = database.recover_stale_analyses(minutes=10)

    recovered_count = len(recovered)
    logger.info("Recovered %d stale analyses", recovered_count)

    for task in recovered:
        logger.info(
            "Recovered stale task (id=%s, repo_name='%s', previous_status='%s', created_at='%s')",
            task.get("id"),
            task.get("repo_name"),
            task.get("status"),
            task.get("created_at")
        )
