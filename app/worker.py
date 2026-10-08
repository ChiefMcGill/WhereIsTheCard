import logging
import os

from app import create_app
from app.tasks.overdue import run_overdue_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        run_overdue_loop(interval_seconds=int(os.getenv("OVERDUE_CHECK_INTERVAL_SECONDS", "60")))
