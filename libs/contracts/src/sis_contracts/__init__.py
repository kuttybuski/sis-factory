from sis_contracts.secrets import load_secret_files
from sis_contracts.settings import Settings

load_secret_files()  # NAME_FILE -> NAME, before any service reads its settings

__all__ = ["Settings"]
