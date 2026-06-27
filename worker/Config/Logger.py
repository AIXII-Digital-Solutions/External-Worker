import datetime
import glob
import logging
import os
import zipfile
from logging.handlers import RotatingFileHandler

from .config import LOGS_DIR, DEV_MODE


class CustomLogHandler(RotatingFileHandler):
    def __init__(self, filename, maxBytes, backupCount):
        self.backup_count = backupCount
        self.log_directory = os.path.dirname(filename)
        self.base_filename = os.path.basename(filename)
        super().__init__(
            filename=filename,
            maxBytes=maxBytes,
            backupCount=0,
            encoding='utf-8',
            delay=False
        )

    def doRollover(self):
        if self.stream:
            self.stream.close()
            self.stream = None

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_name = f"{self.baseFilename}.{timestamp}"
        os.rename(self.baseFilename, archive_name)

        self._archive_file(archive_name)

        self.stream = self._open()

        self._cleanup_logs()

    def _archive_file(self, filename):
        zip_name = f"{filename}.zip"
        with zipfile.ZipFile(zip_name, 'w', zipfile.ZIP_DEFLATED) as zipf:
            zipf.write(filename, os.path.basename(filename))
        os.remove(filename)

    def _cleanup_logs(self):
        zip_files = glob.glob(os.path.join(self.log_directory, "*.zip"))
        zip_files.sort(key=os.path.getctime, reverse=True)

        while len(zip_files) > self.backup_count:
            os.remove(zip_files.pop())


log_format = (
    '%(levelname)s:     [%(name)s] %(asctime)s | %(filename)s-%(lineno)d: %(message)s'
)


def setup_logger(name: str, log_format: str = log_format) -> logging.Logger:
    formatter = logging.Formatter(log_format)

    logger = logging.getLogger(name)

    if DEV_MODE:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    log_file = LOGS_DIR / f"LOG_{datetime.datetime.now().strftime('%Y-%m-%d')}.log"
    file_handler = CustomLogHandler(
        filename=log_file,
        # maxBytes=10 * 1024 * 1024,
        maxBytes=10 * 1024 * 1024,  # 10 MiB
        backupCount=5
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    logger_blacklist = [
        "httpcore.http2",
        "hpack.hpack",
        "hpack.table",
        "asyncio",
        "httpcore.connection",
        "httpx",
    ]

    for module in logger_blacklist:
        logging.getLogger(module).setLevel(logging.ERROR)

    return logger


__all__ = [
    "setup_logger",
]
