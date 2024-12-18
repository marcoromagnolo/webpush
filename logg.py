import logging
from settings import LOG_SETTINGS
import os
from logging.handlers import RotatingFileHandler

def create_logger(name):
    # Logging settings
    app_log_filepath = os.path.join(LOG_SETTINGS['path'], f'{name}.log')
    app_log_level = LOG_SETTINGS['level']
    app_log_formatter = logging.Formatter(LOG_SETTINGS.get('format', '%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    log_dir = os.path.dirname(app_log_filepath)
    if not os.path.exists(log_dir):  # Ensure the log directory exists
        os.makedirs(log_dir)

    # Create logger instance
    logger = logging.getLogger(name)
    logger.setLevel(app_log_level)

    # Create FileHandler to log to file
    file_handler = RotatingFileHandler(app_log_filepath, maxBytes=50*1024*1024, backupCount=5, encoding='utf-8')
    # file_handler = logging.FileHandler(app_log_filepath)
    file_handler.setLevel(app_log_level)
    file_handler.setFormatter(app_log_formatter)
    logger.addHandler(file_handler)

    return logger
