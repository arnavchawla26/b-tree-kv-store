"""Exception types used across the btreekv package."""


class BTreeKVError(Exception):
    """Base class for all btreekv errors."""


class PageOverflowError(BTreeKVError):
    """Raised when a node's encoded form would not fit in a single page.

    This is an internal signal used by the B+Tree implementation to decide
    when a node must be split; callers of the public API should never see
    this exception escape :class:`btreekv.store.KVStore`.
    """


class CorruptPageError(BTreeKVError):
    """Raised when a page read from disk fails its checksum verification."""


class WALCorruptionError(BTreeKVError):
    """Raised for WAL record framing errors that are not simple truncation.

    A truncated trailing record (the expected shape of a crash mid-write) is
    treated as the end of the log, not an error. This exception is reserved
    for records whose framing is internally inconsistent in a way a clean
    crash could not produce (e.g. a corrupted length prefix).
    """
