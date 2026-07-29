"""
logger.py - Logging profesional
"""

import logging
import os
from datetime import datetime

def setup_logger():
    """Configura logger"""
    
    if not os.path.exists('logs'):
        os.makedirs('logs')
    
    fecha = datetime.now().strftime("%Y-%m-%d")
    log_file = f"logs/tradexo_{fecha}.log"
    
    logger = logging.getLogger("TRADEXO")
    logger.setLevel(logging.DEBUG)
    
    formato = logging.Formatter(
        '[%(asctime)s] %(levelname)-8s | %(message)s',
        datefmt='%H:%M:%S'
    )
    
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formato)
    
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formato)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger