import logging
import os
import sys
from pathlib import Path
from typing import Optional


class RelativePathFilter(logging.Filter):
    """
    Custom logging filter that converts absolute file paths to relative paths.

    This filter makes log messages more readable by showing relative paths
    from the project root instead of full absolute paths.
    """

    def __init__(self, base_path: Optional[str] = None):
        """
        Initialize the relative path filter.

        Args:
            base_path (str, optional): Base path to calculate relative paths from.
            Defaults to current working directory.
        """
        super().__init__()
        self.base_path = Path(base_path or os.getcwd())

    def filter(self, record: logging.LogRecord) -> bool:
        """
        Filter log records and modify pathname to show relative path.

        Args:
            record (logging.LogRecord): The log record to filter.

        Returns:
            bool: Always returns True to allow the record through.
        """
        try:
            # Convert absolute path to relative path
            abs_path = Path(record.pathname)
            rel_path = abs_path.relative_to(self.base_path)
            record.relpath = str(rel_path)

        except (ValueError, AttributeError):
            # Fallback to filename if relative path calculation fails
            record.relpath = getattr(record, "filename", record.pathname)

        return True


class LoggingConfig:
    """
    Centralized logging configuration class with singleton pattern.

    This class manages the logging configuration for the entire application,
    ensuring consistent logging behavior across all modules.
    """

    _instance: Optional["LoggingConfig"] = None
    _logger: Optional[logging.Logger] = None
    _is_configured: bool = False

    # Default configuration constants
    DEFAULT_LOG_LEVEL = logging.DEBUG
    DEFAULT_LOG_FORMAT = (
        "%(asctime)s - %(relpath)s:%(lineno)d - " "%(levelname)s - %(message)s"
    )
    DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
    DEFAULT_LOG_FILE = "application.log"
    DEFAULT_LOG_DIR = "logs"

    def __new__(cls) -> "LoggingConfig":
        """
        Implement singleton pattern to ensure only one instance exists.

        Returns:
            LoggingConfig: The singleton instance.
        """
        if cls._instance is None:
            cls._instance = super().__new__(cls)

        return cls._instance

    def __init__(self):
        """
        Initialize the logging configuration if not already done.
        """
        if not hasattr(self, "_initialized"):
            self._initialized = True
            self._project_root = self._find_project_root()
            self._log_dir = self._project_root / self.DEFAULT_LOG_DIR

    def _find_project_root(self) -> Path:
        """
        Find the project root directory by looking for manage.py or settings.py.

        Returns:
            Path: The project root directory path.
        """
        current_path = Path(__file__).resolve()

        # Look for Django project indicators
        for parent in current_path.parents:
            if (parent / "manage.py").exists() or (parent / "settings.py").exists():
                return parent

        # Fallback to current working directory
        return Path.cwd()

    def _ensure_log_directory(self) -> bool:
        """
        Ensure the log directory exists, create it if it doesn't.

        Returns:
            bool: True if directory exists or was created successfully, False otherwise.
        """
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            return True

        except (OSError, PermissionError) as e:
            print(f"Error creating log directory {self._log_dir}: {e}", file=sys.stderr)
            return False

    def _create_formatter(
        self,
        log_format: str = DEFAULT_LOG_FORMAT,
        date_format: str = DEFAULT_DATE_FORMAT,
    ) -> logging.Formatter:
        """
        Create a logging formatter with the specified format.

        Args:
            log_format (str): The log message format string.
            date_format (str): The date format string.

        Returns:
            logging.Formatter: Configured formatter instance.
        """
        return logging.Formatter(fmt=log_format, datefmt=date_format)

    def _create_file_handler(
        self, log_file: str = DEFAULT_LOG_FILE, log_level: int = DEFAULT_LOG_LEVEL
    ) -> Optional[logging.Handler]:
        """
        Create and configure a file handler for logging.

        Args:
            log_file (str): Name of the log file.
            log_level (int): Logging level for the file handler.

        Returns:
            logging.Handler: Configured file handler, or None if creation fails.
        """
        try:
            if not self._ensure_log_directory():
                return None

            log_file_path = self._log_dir / log_file

            # Create file handler with UTF-8 encoding
            file_handler = logging.FileHandler(
                filename=log_file_path, mode="a", encoding="utf-8"
            )

            file_handler.setLevel(log_level)
            file_handler.setFormatter(self._create_formatter())

            # Add relative path filter
            file_handler.addFilter(RelativePathFilter(self._project_root))

            return file_handler

        except (OSError, PermissionError) as e:
            print(f"Error creating file handler for {log_file}: {e}", file=sys.stderr)
            return None

    def _create_console_handler(
        self, log_level: int = DEFAULT_LOG_LEVEL
    ) -> logging.Handler:
        """
        Create and configure a console handler for logging.

        Args:
            log_level (int): Logging level for the console handler.

        Returns:
            logging.Handler: Configured console handler.
        """
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(log_level)
        console_handler.setFormatter(self._create_formatter())

        # Add relative path filter
        console_handler.addFilter(RelativePathFilter(self._project_root))

        return console_handler

    def setup_logging(
        self,
        logger_name: str = "financial_api",
        log_level: int = DEFAULT_LOG_LEVEL,
        log_file: str = DEFAULT_LOG_FILE,
        console_level: int = DEFAULT_LOG_LEVEL,
        file_level: int = DEFAULT_LOG_LEVEL,
        force_reconfigure: bool = False,
    ) -> logging.Logger:
        """
        Set up comprehensive logging configuration with both console and file output.
        Now captures ALL log levels (DEBUG through CRITICAL) for both outputs.
        """
        try:
            # Return existing logger if already configured and not forcing reconfiguration
            if self._is_configured and not force_reconfigure and self._logger:
                return self._logger

            # Get or create logger
            logger = logging.getLogger(logger_name)

            # Clear existing handlers if reconfiguring
            if force_reconfigure:
                logger.handlers.clear()

            # Check if logger already has handlers (avoid duplicate setup)
            if logger.hasHandlers() and not force_reconfigure:
                self._logger = logger
                self._is_configured = True
                return logger

            # Set to DEBUG to capture all levels
            logger.setLevel(log_level)

            # Prevent propagation to root logger to avoid duplicate messages
            logger.propagate = False

            # Create and add console handler
            console_handler = self._create_console_handler(console_level)
            logger.addHandler(console_handler)

            # Create and add file handler (if successful)
            file_handler = self._create_file_handler(log_file, file_level)
            if file_handler:
                logger.addHandler(file_handler)
            else:
                logger.warning(
                    "File logging could not be configured. Only console logging is active."
                )

            # Store configured logger
            self._logger = logger
            self._is_configured = True
            return logger

        except Exception as e:
            error_msg = f"Critical error setting up logging: {e}"
            raise RuntimeError(error_msg) from e

    def get_logger(self, name: str) -> logging.Logger:
        """
        Get a logger instance with the specified name.

        Args:
            name (str): Name of the logger (typically __name__).

        Returns:
            logging.Logger: Logger instance.
        """
        if not self._is_configured:
            # Auto-configure if not already done
            self.setup_logging()

        return logging.getLogger(name)

    def get_log_file_path(self) -> Path:
        """
        Get the full path to the log file.

        Returns:
            Path: Full path to the log file.
        """
        return self._log_dir / self.DEFAULT_LOG_FILE

    def is_configured(self) -> bool:
        """
        Check if logging has been configured.

        Returns:
            bool: True if logging is configured, False otherwise.
        """
        return self._is_configured


# Global instance for easy access
_logging_config = LoggingConfig()


# Convenience functions for backward compatibility and ease of use
def setup_logging(**kwargs) -> logging.Logger:
    """
    Set up logging configuration (convenience function).

    Args:
        **kwargs: Keyword arguments passed to LoggingConfig.setup_logging().

    Returns:
        logging.Logger: Configured logger instance.
    """
    return _logging_config.setup_logging(**kwargs)


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance (convenience function).

    Args:
        name (str): Name of the logger (typically __name__).

    Returns:
        logging.Logger: Logger instance.
    """
    return _logging_config.get_logger(name)


def get_log_file_path() -> Path:
    """
    Get the path to the log file (convenience function).

    Returns:
        Path: Path to the log file.
    """
    return _logging_config.get_log_file_path()
