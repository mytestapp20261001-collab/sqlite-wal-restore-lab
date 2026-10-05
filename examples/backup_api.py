"""Minimal SQLite online-backup example, used with owned synthetic connections."""


def backup_to_connection(source, destination):
    """Back up committed state; caller owns and closes both connections.

    This example does not implement file publication, encryption, retention,
    retry policy, or protection against overwriting a real destination.
    """
    if source.in_transaction or destination.in_transaction:
        raise ValueError("Commit or roll back pending transactions before this example")
    source.backup(destination, pages=-1, sleep=0.0)
