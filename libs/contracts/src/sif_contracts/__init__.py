from sif_contracts.secrets import load_secret_files
from sif_contracts.settings import Settings

load_secret_files()  # NAME_FILE -> NAME, before any service reads its settings

__all__ = ["Settings"]
