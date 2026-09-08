"""Compatibility imports for paper callers; ownership lives in hardy.literature."""

from .literature.client import (
    ArxivClient as ArxivClient,
)
from .literature.client import (
    _http as _http,
)
from .literature.client import (
    _narrow as _narrow,
)
from .literature.library import (
    PaperLibrary as PaperLibrary,
)
from .literature.metadata import (
    ABSTRACT_COLUMNS as ABSTRACT_COLUMNS,
)
from .literature.metadata import (
    ARXIV as ARXIV,
)
from .literature.metadata import (
    ATOM as ATOM,
)
from .literature.metadata import (
    DEFAULT_TIMEOUT_SECONDS as DEFAULT_TIMEOUT_SECONDS,
)
from .literature.metadata import (
    ENDPOINT as ENDPOINT,
)
from .literature.metadata import (
    ERROR_ID as ERROR_ID,
)
from .literature.metadata import (
    LOCK_SECONDS as LOCK_SECONDS,
)
from .literature.metadata import (
    MAX_ARCHIVE_BYTES as MAX_ARCHIVE_BYTES,
)
from .literature.metadata import (
    MAX_RESPONSE_BYTES as MAX_RESPONSE_BYTES,
)
from .literature.metadata import (
    MAX_RESULTS as MAX_RESULTS,
)
from .literature.metadata import (
    MIN_INTERVAL_SECONDS as MIN_INTERVAL_SECONDS,
)
from .literature.metadata import (
    NEW_STYLE as NEW_STYLE,
)
from .literature.metadata import (
    OLD_STYLE as OLD_STYLE,
)
from .literature.metadata import (
    QUERY_TTL_SECONDS as QUERY_TTL_SECONDS,
)
from .literature.metadata import (
    READ_CHUNK_BYTES as READ_CHUNK_BYTES,
)
from .literature.metadata import (
    SOURCE_ARCHIVE as SOURCE_ARCHIVE,
)
from .literature.metadata import (
    SOURCE_DIR as SOURCE_DIR,
)
from .literature.metadata import (
    SOURCE_ENDPOINT as SOURCE_ENDPOINT,
)
from .literature.metadata import (
    SOURCE_MANIFEST as SOURCE_MANIFEST,
)
from .literature.metadata import (
    STRIP as STRIP,
)
from .literature.metadata import (
    USER_AGENT as USER_AGENT,
)
from .literature.metadata import (
    ArxivError as ArxivError,
)
from .literature.metadata import (
    ArxivId as ArxivId,
)
from .literature.metadata import (
    PaperRecord as PaperRecord,
)
from .literature.metadata import (
    SourceFile as SourceFile,
)
from .literature.metadata import (
    SourceManifest as SourceManifest,
)
from .literature.metadata import (
    Transport as Transport,
)
from .literature.metadata import (
    _by_bytes as _by_bytes,
)
from .literature.metadata import (
    _coherent as _coherent,
)
from .literature.metadata import (
    _collapsed as _collapsed,
)
from .literature.metadata import (
    _decoded as _decoded,
)
from .literature.metadata import (
    _entries as _entries,
)
from .literature.metadata import (
    _entry as _entry,
)
from .literature.metadata import (
    _key as _key,
)
from .literature.metadata import (
    _parsed as _parsed,
)
from .literature.metadata import (
    _stamp as _stamp,
)
from .literature.metadata import (
    _text as _text,
)
from .literature.metadata import (
    _wrapped as _wrapped,
)
from .literature.metadata import (
    digest as digest,
)
from .literature.metadata import (
    parse_id as parse_id,
)
