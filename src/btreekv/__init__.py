"""btreekv: an embedded key-value store built on a from-scratch on-disk B+Tree.

Public API is exposed through :mod:`btreekv.store`.
"""

from .store import KVStore
from .errors import BTreeKVError, CorruptPageError, PageOverflowError

__all__ = ["KVStore", "BTreeKVError", "CorruptPageError", "PageOverflowError"]

__version__ = "0.1.0"
