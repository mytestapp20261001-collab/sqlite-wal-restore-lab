"""Intentionally wrong for a live WAL database. Synthetic negative control only."""
import shutil


def copy_main_file(source_path, destination_path):
    """Omit WAL on purpose; do not use as a production backup recipe."""
    shutil.copyfile(source_path, destination_path)
