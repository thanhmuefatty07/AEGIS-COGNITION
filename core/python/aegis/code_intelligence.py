"""Bounded local source mapping for the Living Workspace.

The mapper emits a read-only, hash-bound projection. Extracted calls and
lineage are candidates; they are never presented as compiler-proven facts.
"""

from __future__ import annotations

import ast
import ctypes
import errno
import hashlib
import json
import ntpath
import os
import re
import stat as stat_module
import subprocess
import threading
import time
from collections import deque
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Protocol, Self

MAX_FILES = 10_000
MAX_DIRECTORIES = 10_000
MAX_TRAVERSAL_ENTRIES = MAX_FILES + MAX_DIRECTORIES
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_GIT_METADATA_BYTES = 2 * 1024 * 1024
MAX_GIT_METADATA_PATHS = MAX_FILES
MAX_SYMBOLS_PER_FILE = 2_000
MAX_IMPORTS_PER_FILE = 1_000
MAX_WATCH_EVENTS = 2_048
_WINDOWS_STATUS_REPARSE_POINT_ENCOUNTERED = 0xC000050B
MAX_REPO_MAP_FILES = 64
MAX_REPO_MAP_SYMBOLS_PER_FILE = 64
MAX_REPO_MAP_IMPORTS_PER_FILE = 32
MAX_REPO_MAP_QUERY_LENGTH = 8_192
IGNORED_DIRECTORIES = frozenset(
    {
        ".aegis",
        ".cache",
        ".git",
        ".hg",
        ".local",
        ".private",
        ".svn",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "generated",
        "node_modules",
        "private",
        "secrets",
        "target",
        "vendor",
    }
)
_SOURCE_LANGUAGES = {
    ".js": "javascript",
    ".jsx": "javascript",
    ".py": "python",
    ".rs": "rust",
    ".ts": "typescript",
    ".tsx": "typescript",
}
_IGNORE_CHECK_BATCH_SIZE = 512


class CodeIntelligenceError(ValueError):
    """The source projection cannot be created safely."""


class SourceFileReadError(CodeIntelligenceError):
    """A bounded workspace read failed its containment or consistency checks."""

    def __init__(
        self,
        reason: str,
        message: str,
        *,
        file_stat: os.stat_result | None = None,
        bytes_read: int = 0,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.file_stat = file_stat
        self.bytes_read = bytes_read


class _SourceTraversalLimit(RuntimeError):
    """The filesystem walk exceeded its directory or source-candidate budget."""


class _DirectoryEntryStat(Protocol):
    st_mode: int


class _DirectoryEntry(Protocol):
    name: str

    def stat(self, *, follow_symlinks: bool = True) -> _DirectoryEntryStat: ...


@lru_cache(maxsize=1)
def _windows_final_path_api() -> tuple[object, object, object, object, object, object]:
    from ctypes import wintypes

    win_dll = ctypes.WinDLL
    kernel32 = win_dll("kernel32", use_last_error=True)
    get_final_path = kernel32.GetFinalPathNameByHandleW
    get_final_path.argtypes = (
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    get_final_path.restype = wintypes.DWORD
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    get_file_information_ex = kernel32.GetFileInformationByHandleEx
    get_file_information_ex.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    get_file_information_ex.restype = wintypes.BOOL
    get_file_information = kernel32.GetFileInformationByHandle
    get_file_information.argtypes = (wintypes.HANDLE, wintypes.LPVOID)
    get_file_information.restype = wintypes.BOOL
    return kernel32, get_final_path, create_file, close_handle, get_file_information_ex, get_file_information


@lru_cache(maxsize=1)
def _windows_nt_file_api() -> tuple[object, object]:
    from ctypes import wintypes

    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    create_file = ntdll.NtCreateFile
    create_file.argtypes = (
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.ULONG,
        wintypes.LPVOID,
        wintypes.LPVOID,
        ctypes.POINTER(ctypes.c_longlong),
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.LPVOID,
        wintypes.ULONG,
    )
    create_file.restype = ctypes.c_long
    status_to_error = ntdll.RtlNtStatusToDosError
    status_to_error.argtypes = (ctypes.c_long,)
    status_to_error.restype = wintypes.ULONG
    return create_file, status_to_error


def _windows_handle_has_reparse_point(handle: object, api: tuple[object, ...]) -> bool:
    from ctypes import wintypes

    class FileTime(ctypes.Structure):
        _fields_ = (("LowDateTime", wintypes.DWORD), ("HighDateTime", wintypes.DWORD))

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = (
            ("FileAttributes", wintypes.DWORD),
            ("CreationTime", FileTime),
            ("LastAccessTime", FileTime),
            ("LastWriteTime", FileTime),
            ("VolumeSerialNumber", wintypes.DWORD),
            ("FileSizeHigh", wintypes.DWORD),
            ("FileSizeLow", wintypes.DWORD),
            ("NumberOfLinks", wintypes.DWORD),
            ("FileIndexHigh", wintypes.DWORD),
            ("FileIndexLow", wintypes.DWORD),
        )

    information = ByHandleFileInformation()
    if not api[5](handle, ctypes.byref(information)):  # type: ignore[operator]
        raise ctypes.WinError(ctypes.get_last_error())
    return bool(information.FileAttributes & stat_module.FILE_ATTRIBUTE_REPARSE_POINT)


def _windows_nt_path(path: str) -> str:
    value = ntpath.normpath(path)
    if "\x00" in value or value.startswith("\\\\.\\"):
        raise SourceFileReadError("unsafe_path", "workspace root uses an unsupported Windows path")
    if value[:8].casefold() == "\\\\?\\unc\\":
        return "\\??\\UNC\\" + value[8:]
    if value.startswith("\\\\?\\"):
        extended_name = value[4:]
        drive, tail = ntpath.splitdrive(extended_name)
        if (len(drive) == 2 and drive[1] == ":" and tail.startswith("\\")) or extended_name.casefold().startswith(
            "volume{"
        ):
            return "\\??\\" + extended_name
        raise SourceFileReadError("unsafe_path", "workspace root uses an unsupported Windows namespace")
    if value.startswith("\\\\"):
        return "\\??\\UNC\\" + value[2:]
    drive, tail = ntpath.splitdrive(value)
    if len(drive) == 2 and drive[1] == ":" and tail.startswith("\\"):
        return "\\??\\" + value
    raise SourceFileReadError("unsafe_path", "workspace root is not a supported absolute Windows path")


def _windows_open_file_beneath_root(root_path: Path, parts: tuple[str, ...]) -> int:
    from ctypes import wintypes
    import msvcrt

    class UnicodeString(ctypes.Structure):
        _fields_ = (("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", wintypes.LPWSTR))

    class ObjectAttributes(ctypes.Structure):
        _fields_ = (
            ("Length", wintypes.ULONG),
            ("RootDirectory", wintypes.HANDLE),
            ("ObjectName", ctypes.POINTER(UnicodeString)),
            ("Attributes", wintypes.ULONG),
            ("SecurityDescriptor", wintypes.LPVOID),
            ("SecurityQualityOfService", wintypes.LPVOID),
        )

    class IoStatusBlockUnion(ctypes.Union):
        _fields_ = (("Status", ctypes.c_long), ("Pointer", wintypes.LPVOID))

    class IoStatusBlock(ctypes.Structure):
        _fields_ = (("Value", IoStatusBlockUnion), ("Information", ctypes.c_size_t))

    api = _windows_final_path_api()
    create_file, status_to_error = _windows_nt_file_api()

    def open_native_path(parent: object | None, name: str, *, directory: bool) -> object:
        encoded_length = len(name.encode("utf-16-le", "surrogatepass"))
        if encoded_length == 0 or encoded_length > 0xFFFE:
            raise SourceFileReadError("unsafe_path", "workspace path exceeds the supported Windows limit")
        name_buffer = ctypes.create_unicode_buffer(name)
        object_name = UnicodeString(encoded_length, encoded_length, ctypes.cast(name_buffer, wintypes.LPWSTR))
        attributes = ObjectAttributes(
            ctypes.sizeof(ObjectAttributes),
            parent,
            ctypes.pointer(object_name),
            0x40 | 0x1000,  # OBJ_CASE_INSENSITIVE | OBJ_DONT_REPARSE
            None,
            None,
        )
        io_status = IoStatusBlock()
        handle = wintypes.HANDLE()
        status = create_file(  # type: ignore[operator]
            ctypes.byref(handle),
            0x001000A0
            if directory
            else 0x00100081,  # FILE_TRAVERSE/FILE_READ_DATA | FILE_READ_ATTRIBUTES | SYNCHRONIZE
            ctypes.byref(attributes),
            ctypes.byref(io_status),
            None,
            0,
            0x7,  # FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
            1,  # FILE_OPEN
            (0x1 if directory else 0x40)
            | 0x20,  # FILE_DIRECTORY_FILE or FILE_NON_DIRECTORY_FILE | FILE_SYNCHRONOUS_IO_NONALERT
            None,
            0,
        )
        if status < 0:
            if status & 0xFFFFFFFF == _WINDOWS_STATUS_REPARSE_POINT_ENCOUNTERED:
                raise SourceFileReadError("unsafe_path", "workspace path contains a reparse point")
            raise ctypes.WinError(int(status_to_error(status)))  # type: ignore[operator]
        return handle

    root_handle = wintypes.HANDLE()
    file_handle = wintypes.HANDLE()
    descriptor: int | None = None
    try:
        root_handle = open_native_path(None, _windows_nt_path(str(root_path)), directory=True)
        if _windows_handle_has_reparse_point(root_handle, api):
            raise SourceFileReadError("unsafe_path", "workspace root is a reparse point")
        actual_root = _windows_path_key(_windows_final_handle_path(root_handle, api[1]))
        if actual_root != _windows_path_key(str(root_path)):
            raise SourceFileReadError("unsafe_path", "workspace root changed before the file open")

        relative_name = "\\".join(parts)
        file_handle = open_native_path(root_handle, relative_name, directory=False)
        if _windows_handle_has_reparse_point(file_handle, api):
            raise SourceFileReadError("unsafe_path", "workspace file is a reparse point")
        descriptor = msvcrt.open_osfhandle(file_handle.value, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        return descriptor
    finally:
        cleanup_error: Exception | None = None
        if file_handle.value and descriptor is None:
            try:
                if not api[3](file_handle):  # type: ignore[operator]
                    get_last_error = getattr(ctypes, "get_last_error", lambda: 5)
                    win_error = getattr(ctypes, "WinError", OSError)
                    cleanup_error = win_error(get_last_error())
            except Exception as error:
                cleanup_error = error
        if root_handle.value:
            try:
                if not api[3](root_handle):  # type: ignore[operator]
                    get_last_error = getattr(ctypes, "get_last_error", lambda: 5)
                    win_error = getattr(ctypes, "WinError", OSError)
                    root_close_error = win_error(get_last_error())
                    if cleanup_error is None:
                        cleanup_error = root_close_error
            except Exception as error:
                if cleanup_error is None:
                    cleanup_error = error
        if cleanup_error is not None:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except Exception as descriptor_close_error:
                    raise cleanup_error from descriptor_close_error
            raise cleanup_error


def _windows_handle_path(file_descriptor: int) -> str:
    import msvcrt
    from ctypes import wintypes

    api = _windows_final_path_api()
    get_final_path = api[1]
    handle = wintypes.HANDLE(msvcrt.get_osfhandle(file_descriptor))
    return _windows_final_handle_path(handle, get_final_path)


def _windows_final_handle_path(handle: object, get_final_path: object) -> str:
    capacity = 260
    while capacity <= 32_768:
        buffer = ctypes.create_unicode_buffer(capacity)
        length = get_final_path(handle, buffer, capacity, 0)  # type: ignore[operator]
        if length == 0:
            raise OSError(ctypes.get_last_error(), "opened workspace path could not be resolved")
        if length < capacity:
            return buffer.value
        capacity = length + 1
    raise OSError(errno.ENAMETOOLONG, "opened workspace path exceeds the supported limit")


def _windows_path_key(value: str) -> str:
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return ntpath.normcase(ntpath.normpath(value))


def _open_posix_directory(path: Path) -> int:
    if path.anchor != os.sep or os.open not in os.supports_dir_fd:
        raise OSError(errno.ENOTSUP, "descriptor-relative workspace reads are unavailable")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(path.anchor, directory_flags)
    try:
        for component in path.parts[1:]:
            child_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = child_fd
    except BaseException:
        os.close(directory_fd)
        raise
    return directory_fd


@dataclass(frozen=True, slots=True)
class _WindowsScandirStat:
    st_mode: int
    st_file_attributes: int


@dataclass(frozen=True, slots=True)
class _WindowsScandirEntry:
    name: str
    file_attributes: int

    def stat(self, *, follow_symlinks: bool = True) -> _WindowsScandirStat:
        if follow_symlinks:
            raise ValueError("Windows directory entries expose no-follow metadata only")
        if self.file_attributes & stat_module.FILE_ATTRIBUTE_REPARSE_POINT:
            mode = stat_module.S_IFLNK
        elif self.file_attributes & stat_module.FILE_ATTRIBUTE_DIRECTORY:
            mode = stat_module.S_IFDIR
        else:
            mode = stat_module.S_IFREG
        return _WindowsScandirStat(mode, self.file_attributes)


def _windows_open_directory_handle(path: Path) -> object:
    from ctypes import wintypes

    class UnicodeString(ctypes.Structure):
        _fields_ = (("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", wintypes.LPWSTR))

    class ObjectAttributes(ctypes.Structure):
        _fields_ = (
            ("Length", wintypes.ULONG),
            ("RootDirectory", wintypes.HANDLE),
            ("ObjectName", ctypes.POINTER(UnicodeString)),
            ("Attributes", wintypes.ULONG),
            ("SecurityDescriptor", wintypes.LPVOID),
            ("SecurityQualityOfService", wintypes.LPVOID),
        )

    class IoStatusBlockUnion(ctypes.Union):
        _fields_ = (("Status", ctypes.c_long), ("Pointer", wintypes.LPVOID))

    class IoStatusBlock(ctypes.Structure):
        _fields_ = (("Value", IoStatusBlockUnion), ("Information", ctypes.c_size_t))

    api = _windows_final_path_api()
    create_file, status_to_error = _windows_nt_file_api()
    native_name = _windows_nt_path(str(path))
    encoded_length = len(native_name.encode("utf-16-le", "surrogatepass"))
    if encoded_length == 0 or encoded_length > 0xFFFE:
        raise OSError(errno.ENAMETOOLONG, "workspace directory path exceeds the supported Windows limit", str(path))

    name_buffer = ctypes.create_unicode_buffer(native_name)
    object_name = UnicodeString(encoded_length, encoded_length, ctypes.cast(name_buffer, wintypes.LPWSTR))
    attributes = ObjectAttributes(
        ctypes.sizeof(ObjectAttributes),
        None,
        ctypes.pointer(object_name),
        0x40 | 0x1000,  # OBJ_CASE_INSENSITIVE | OBJ_DONT_REPARSE
        None,
        None,
    )
    io_status = IoStatusBlock()
    handle = wintypes.HANDLE()
    status = create_file(
        ctypes.byref(handle),
        0x00100081,  # FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES | SYNCHRONIZE
        ctypes.byref(attributes),
        ctypes.byref(io_status),
        None,
        0,
        0x7,  # FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
        1,  # FILE_OPEN
        0x1 | 0x20,  # FILE_DIRECTORY_FILE | FILE_SYNCHRONOUS_IO_NONALERT
        None,
        0,
    )
    if status < 0:
        if handle.value:
            api[3](handle)  # type: ignore[operator]
        if status & 0xFFFFFFFF == _WINDOWS_STATUS_REPARSE_POINT_ENCOUNTERED:
            raise OSError(errno.EPERM, "workspace directory path contains a reparse point", str(path))
        raise ctypes.WinError(int(status_to_error(status)))  # type: ignore[operator]
    return handle


def _windows_directory_entries(handle: object, api: tuple[object, ...]) -> Iterator[_DirectoryEntry]:
    class FileFullDirectoryInfo(ctypes.Structure):
        _fields_ = (
            ("NextEntryOffset", ctypes.c_uint32),
            ("FileIndex", ctypes.c_uint32),
            ("CreationTime", ctypes.c_int64),
            ("LastAccessTime", ctypes.c_int64),
            ("LastWriteTime", ctypes.c_int64),
            ("ChangeTime", ctypes.c_int64),
            ("EndOfFile", ctypes.c_int64),
            ("AllocationSize", ctypes.c_int64),
            ("FileAttributes", ctypes.c_uint32),
            ("FileNameLength", ctypes.c_uint32),
            ("EaSize", ctypes.c_uint32),
            ("FileName", ctypes.c_uint16 * 1),
        )

    buffer_size = 64 * 1024
    buffer = ctypes.create_string_buffer(buffer_size + 7)
    buffer_address = (ctypes.addressof(buffer) + 7) & ~7
    buffer_pointer = ctypes.c_void_p(buffer_address)
    filename_offset = FileFullDirectoryInfo.FileName.offset

    while True:
        if not api[4](handle, 0xE, buffer_pointer, buffer_size):  # type: ignore[operator]
            get_last_error = getattr(ctypes, "get_last_error", lambda: 18)
            error = get_last_error()
            if error == 18:  # ERROR_NO_MORE_FILES
                return
            win_error = getattr(ctypes, "WinError", OSError)
            raise win_error(error)

        offset = 0
        entries_in_buffer = 0
        while True:
            if offset > buffer_size - filename_offset:
                raise OSError(errno.EIO, "Windows returned a truncated directory entry")
            entry = FileFullDirectoryInfo.from_address(buffer_address + offset)
            filename_length = int(entry.FileNameLength)
            next_offset = int(entry.NextEntryOffset)
            if filename_length == 0 or filename_length % 2 or filename_length > buffer_size - offset - filename_offset:
                raise OSError(errno.EIO, "Windows returned an invalid or empty directory entry name length")
            if next_offset and (
                next_offset % 8 or next_offset < filename_offset + filename_length or next_offset > buffer_size - offset
            ):
                raise OSError(errno.EIO, "Windows returned an invalid directory entry offset")

            raw_name = ctypes.string_at(buffer_address + offset + filename_offset, filename_length)
            name = raw_name.decode("utf-16-le", "surrogatepass")
            if name not in {".", ".."}:
                yield _WindowsScandirEntry(name, int(entry.FileAttributes))
            entries_in_buffer += 1
            if next_offset == 0:
                break
            offset += next_offset

        if entries_in_buffer == 0:
            raise OSError(errno.EIO, "Windows returned an empty successful directory query")


@contextmanager
def _scandir_directory(path: Path) -> Iterator[Iterator[_DirectoryEntry]]:
    if os.name == "nt":
        api = _windows_final_path_api()
        handle = _windows_open_directory_handle(path)
        try:
            if _windows_handle_has_reparse_point(handle, api):
                raise OSError(errno.EPERM, "workspace directory is a reparse point", str(path))
            actual = _windows_path_key(_windows_final_handle_path(handle, api[1]))
            expected = _windows_path_key(str(path))
            if actual != expected:
                raise OSError(errno.EPERM, "workspace directory changed or resolves outside its path", str(path))
            yield _windows_directory_entries(handle, api)
            actual_after = _windows_path_key(_windows_final_handle_path(handle, api[1]))
            if actual_after != expected:
                raise OSError(errno.EPERM, "workspace directory moved during scan", str(path))
        finally:
            if not api[3](handle):  # type: ignore[operator]
                raise ctypes.WinError(ctypes.get_last_error())
        return

    directory_fd = _open_posix_directory(path)
    try:
        iterator = os.scandir(directory_fd)
        with iterator:
            yield iterator
    finally:
        os.close(directory_fd)


def read_file_beneath_root(
    root: str | Path,
    relative_path: str,
    *,
    max_bytes: int,
) -> tuple[os.stat_result, bytes]:
    """Read one file through a stable root-relative handle and enforce byte bounds."""

    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
        raise ValueError("max_bytes must be a non-negative integer")
    if (
        not isinstance(relative_path, str)
        or not relative_path
        or "\x00" in relative_path
        or any(part in {"", ".", ".."} for part in relative_path.split("/"))
    ):
        raise SourceFileReadError("unsafe_path", "workspace path is not a normalized relative file")
    root_path = Path(root)
    if not root_path.is_absolute():
        raise ValueError("root must be absolute")
    parts = tuple(relative_path.split("/"))
    if os.name == "nt":
        relative = Path(relative_path)
        if "\\" in relative_path or relative.is_absolute() or relative.drive:
            raise SourceFileReadError("unsafe_path", "workspace path is not a normalized relative file")
    target = root_path.joinpath(*parts)
    descriptor: int | None = None
    parent_fd: int | None = None
    prior_stat: os.stat_result | None = None
    try:
        read_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        if os.name == "nt":
            descriptor = _windows_open_file_beneath_root(root_path, parts)
        else:
            parent_fd = _open_posix_directory(root_path)
            directory_flags = read_flags | getattr(os, "O_DIRECTORY", 0)
            for component in parts[:-1]:
                child_fd = os.open(component, directory_flags, dir_fd=parent_fd)
                os.close(parent_fd)
                parent_fd = child_fd
            try:
                prior_stat = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
            except OSError as error:
                raise SourceFileReadError("stat_error", "workspace file metadata is unavailable") from error
            if stat_module.S_ISLNK(prior_stat.st_mode):
                raise SourceFileReadError("unsafe_path", "workspace links cannot be read", file_stat=prior_stat)
            if not stat_module.S_ISREG(prior_stat.st_mode):
                raise SourceFileReadError("unsupported", "workspace source is not a regular file", file_stat=prior_stat)
            descriptor = os.open(parts[-1], read_flags, dir_fd=parent_fd)
        opened_stat = os.fstat(descriptor)
        expected_path: str | None = None
        if os.name == "nt":
            expected_path = _windows_path_key(str(target))
            try:
                actual_path = _windows_path_key(_windows_handle_path(descriptor))
            except OSError as error:
                raise SourceFileReadError("unsafe_path", "opened workspace path could not be verified") from error
            if actual_path != expected_path:
                raise SourceFileReadError("unsafe_path", "opened file is outside its validated workspace path")
        if not stat_module.S_ISREG(opened_stat.st_mode):
            raise SourceFileReadError("unsupported", "workspace source is not a regular file", file_stat=opened_stat)
        if opened_stat.st_nlink != 1:
            raise SourceFileReadError(
                "unsafe_path", "workspace files must have exactly one link", file_stat=opened_stat
            )
        if prior_stat is not None and (
            prior_stat.st_dev != opened_stat.st_dev
            or (prior_stat.st_ino and opened_stat.st_ino and prior_stat.st_ino != opened_stat.st_ino)
        ):
            raise SourceFileReadError("unsafe_path", "workspace file changed during open", file_stat=opened_stat)
        if opened_stat.st_size > max_bytes:
            raise SourceFileReadError("too_large", "workspace file exceeds the read limit", file_stat=opened_stat)
        source_file = os.fdopen(descriptor, "rb")
        descriptor = None
        with source_file:
            raw = source_file.read(max_bytes + 1)
            after_stat = os.fstat(source_file.fileno())
            if after_stat.st_size > max_bytes or len(raw) > max_bytes:
                raise SourceFileReadError(
                    "too_large",
                    "workspace file exceeds the read limit",
                    file_stat=after_stat,
                    bytes_read=len(raw),
                )
            if len(raw) != after_stat.st_size or (
                opened_stat.st_size,
                opened_stat.st_mtime_ns,
                opened_stat.st_ctime_ns,
            ) != (
                after_stat.st_size,
                after_stat.st_mtime_ns,
                after_stat.st_ctime_ns,
            ):
                raise SourceFileReadError(
                    "changed",
                    "workspace file changed during read",
                    file_stat=after_stat,
                    bytes_read=len(raw),
                )
            if expected_path is not None:
                try:
                    actual_path = _windows_path_key(_windows_handle_path(source_file.fileno()))
                except OSError as error:
                    raise SourceFileReadError(
                        "unsafe_path",
                        "opened workspace path could not be verified",
                        file_stat=after_stat,
                        bytes_read=len(raw),
                    ) from error
                if actual_path != expected_path:
                    raise SourceFileReadError(
                        "unsafe_path",
                        "opened file moved outside its validated workspace path",
                        file_stat=after_stat,
                        bytes_read=len(raw),
                    )
        return after_stat, raw
    except SourceFileReadError:
        raise
    except OSError as error:
        reason = "unsafe_path" if error.errno in {errno.ELOOP, errno.ENOTDIR} else "unavailable"
        raise SourceFileReadError(reason, "workspace file could not be read") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent_fd is not None:
            os.close(parent_fd)


@dataclass(frozen=True)
class ProjectIdentity:
    project_id: str
    root: str
    vcs: str
    repository_root: str | None
    common_git_dir: str | None
    checkout_id: str
    branch: str | None
    head: str | None
    dirty: bool
    dirty_paths: tuple[str, ...]


@dataclass(frozen=True)
class SymbolRecord:
    name: str
    kind: str
    line: int
    signature_hash: str


@dataclass(frozen=True)
class SourceFileRecord:
    relative_path: str
    language: str
    size_bytes: int
    modified_ns: int
    content_hash: str | None
    extraction_status: str
    symbols: tuple[SymbolRecord, ...] = ()
    imports: tuple[str, ...] = ()
    error: str | None = None
    symlink_target: str | None = None


@dataclass
class _SourceReadAccounting:
    bytes_read: int = 0


@dataclass(frozen=True)
class LineageCandidate:
    source_path: str
    symbol: str
    candidate_paths: tuple[str, ...]
    reason: str
    certainty: str = "CANDIDATE"


@dataclass(frozen=True)
class SourceSnapshot:
    identity: ProjectIdentity
    revision: str
    created_at_ms: int
    files: tuple[SourceFileRecord, ...]
    lineage: tuple[LineageCandidate, ...]
    stale_paths: tuple[str, ...] = ()
    deleted_paths: tuple[str, ...] = ()
    overflowed: bool = False


@dataclass(frozen=True)
class RepositoryMapEntry:
    """A bounded symbol-level projection of one source file."""

    relative_path: str
    language: str
    size_bytes: int
    content_hash: str | None
    extraction_status: str
    score: int
    symbols: tuple[SymbolRecord, ...]
    imports: tuple[str, ...]
    token_cost: int


@dataclass(frozen=True)
class RepositoryMap:
    """A deterministic, revision-bound map suitable for model context."""

    schema: str
    source_revision: str
    query: str
    token_budget: int
    token_count: int
    accounting: str
    complete: bool
    entries: tuple[RepositoryMapEntry, ...]
    omitted_files: int
    rendered: str
    map_hash: str


@dataclass(frozen=True)
class SnapshotDelta:
    added: tuple[str, ...]
    changed: tuple[str, ...]
    deleted: tuple[str, ...]
    renamed: tuple[tuple[str, str], ...]
    copied: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class WatchEvent:
    path: str
    kind: str


class BoundedSnapshotWatcher:
    """Bounded event hint queue; overflow requests a complete rescan."""

    def __init__(self, max_events: int = MAX_WATCH_EVENTS) -> None:
        if isinstance(max_events, bool) or not isinstance(max_events, int) or max_events < 1:
            raise ValueError("max_events must be a positive integer")
        self._events: deque[WatchEvent] = deque(maxlen=max_events)
        self._max_events = max_events
        self.overflowed = False

    def notify(self, path: str, kind: str = "changed") -> bool:
        if not isinstance(path, str) or not path.strip() or "\x00" in path:
            raise ValueError("watch path must be a non-empty string")
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError("watch event kind must be a non-empty string")
        if len(self._events) >= self._max_events:
            self.overflowed = True
            return False
        self._events.append(WatchEvent(path, kind))
        return True

    def drain(self) -> tuple[WatchEvent, ...]:
        events = tuple(self._events)
        self._events.clear()
        return events

    def consume_overflow(self) -> bool:
        overflowed = self.overflowed
        self.overflowed = False
        return overflowed

    def mark_overflow(self) -> None:
        """Force the next snapshot to perform a complete rescan."""

        self.overflowed = True


class NativeSnapshotWatcher:
    """Rust-backed watcher adapter that feeds the bounded Python projection."""

    def __init__(self, root: str | Path, *, max_events: int = MAX_WATCH_EVENTS) -> None:
        if isinstance(max_events, bool) or not isinstance(max_events, int) or max_events < 1:
            raise ValueError("max_events must be a positive integer")
        try:
            from .native import native_module
        except ImportError:
            from aegis.native import native_module  # type: ignore[import-not-found]
        self._native = native_module()
        self._max_events = min(max_events, MAX_WATCH_EVENTS)
        self._token = self._native.aegis_start_source_watcher(str(Path(root).expanduser().resolve()))
        self._closed = False

    def poll(self) -> BoundedSnapshotWatcher:
        if self._closed:
            raise CodeIntelligenceError("native source watcher is closed")
        raw = self._native.aegis_poll_source_watcher(self._token, self._max_events)
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError) as error:
            raise CodeIntelligenceError("native source watcher returned invalid JSON") from error
        if not isinstance(payload, dict) or payload.get("schema") != "aegis-source-watch-events-v1":
            raise CodeIntelligenceError("native source watcher returned an invalid schema")
        events = payload.get("events")
        if not isinstance(events, list) or not isinstance(payload.get("overflowed"), bool):
            raise CodeIntelligenceError("native source watcher returned invalid events")
        bounded = BoundedSnapshotWatcher(self._max_events)
        for event in events:
            if not isinstance(event, dict) or not isinstance(event.get("path"), str):
                raise CodeIntelligenceError("native source watcher returned an invalid path")
            bounded.notify(event["path"], str(event.get("kind", "changed")))
        if payload["overflowed"]:
            bounded.mark_overflow()
        return bounded

    def close(self) -> bool:
        if self._closed:
            return False
        self._closed = True
        return bool(self._native.aegis_stop_source_watcher(self._token))

    def __enter__(self) -> Self:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()


class SourceMapper:
    """Create deterministic source snapshots with bounded local I/O."""

    def __init__(
        self,
        root: str | Path,
        *,
        max_files: int = MAX_FILES,
        max_file_bytes: int = MAX_FILE_BYTES,
        max_total_bytes: int = MAX_TOTAL_BYTES,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise CodeIntelligenceError("source root must be an existing directory")
        for value, name in (
            (max_files, "max_files"),
            (max_file_bytes, "max_file_bytes"),
            (max_total_bytes, "max_total_bytes"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        self.max_files = max_files
        self.max_file_bytes = max_file_bytes
        self.max_total_bytes = max_total_bytes

    def identity(self) -> ProjectIdentity:
        git = _git_identity(self.root)
        dirty_candidates = tuple(path for path in git.dirty_paths if _is_source_path(path))
        if git.repository_root and dirty_candidates:
            safe_candidates: list[str] = []
            for relative_path in dirty_candidates:
                try:
                    read_file_beneath_root(self.root, relative_path, max_bytes=0)
                except SourceFileReadError as error:
                    if error.reason in {"unsafe_path", "unsupported"}:
                        continue
                    # TOO_LARGE proves the no-follow open succeeded; other Git paths may be deleted or unreadable.
                safe_candidates.append(relative_path)
            dirty_candidates = tuple(safe_candidates)
            ignored = self._git_ignored_paths(dirty_candidates)
            dirty_candidates = tuple(path for path in dirty_candidates if path not in ignored)
        root_key = str(self.root).casefold()
        project_id = _digest("aegis-project-v1", root_key, git.repository_root or "")
        checkout_id = _digest(
            "aegis-checkout-v1",
            project_id,
            git.common_git_dir or "",
            git.checkout_path,
            git.head or "",
            git.branch or "",
        )
        return ProjectIdentity(
            project_id=project_id,
            root=str(self.root),
            vcs="git" if git.repository_root else "none",
            repository_root=git.repository_root,
            common_git_dir=git.common_git_dir,
            checkout_id=checkout_id,
            branch=git.branch,
            head=git.head,
            dirty=bool(dirty_candidates),
            dirty_paths=dirty_candidates,
        )

    def snapshot(
        self,
        previous: SourceSnapshot | None = None,
        *,
        watcher: BoundedSnapshotWatcher | None = None,
    ) -> SourceSnapshot:
        identity = self.identity()
        files: list[SourceFileRecord] = []
        total_bytes = 0
        scanned_files = 0
        previous_by_path = (
            {_normalized_source_path(item.relative_path): item for item in previous.files}
            if previous is not None
            else {}
        )
        stale_paths: list[str] = []
        overflowed = watcher.consume_overflow() if watcher is not None else False
        if watcher is not None:
            for event in watcher.drain():
                relative = _relative_event_path(self.root, event.path)
                if relative is None:
                    overflowed = True
        try:
            for path in self._walk_files(use_git_ignore=identity.vcs == "git"):
                relative = path.relative_to(self.root).as_posix()
                if scanned_files >= self.max_files:
                    overflowed = True
                    break
                scanned_files += 1
                prior = previous_by_path.get(_normalized_source_path(relative))
                accounting = _SourceReadAccounting()
                record = self._extract(path, relative, total_bytes, previous=prior, accounting=accounting)
                total_bytes += accounting.bytes_read
                if record is None:
                    continue
                if record.extraction_status in {"BINARY_OR_INVALID_UTF8", "UNSUPPORTED_FILE_TYPE"}:
                    if total_bytes > self.max_total_bytes:
                        overflowed = True
                        break
                    continue
                if record.extraction_status == "SIZE_LIMIT":
                    overflowed = True
                if record.extraction_status == "SYNTAX_ERROR" and prior is not None:
                    record = replace(
                        record,
                        extraction_status="SYNTAX_ERROR_STALE_FALLBACK",
                        symbols=prior.symbols,
                        imports=prior.imports,
                    )
                if prior is not None and prior.content_hash != record.content_hash:
                    stale_paths.append(relative)
                files.append(record)
                if total_bytes > self.max_total_bytes:
                    overflowed = True
                    break
        except _SourceTraversalLimit:
            overflowed = True
        files.sort(key=lambda item: item.relative_path)
        lineage = _build_lineage(files)
        current_paths = {_normalized_source_path(item.relative_path) for item in files}
        # A bounded/failed scan cannot distinguish deletion from an omitted file.
        deleted = (
            tuple(sorted(record.relative_path for key, record in previous_by_path.items() if key not in current_paths))
            if previous is not None and not overflowed
            else ()
        )
        revision = _snapshot_revision(identity, files, lineage)
        return SourceSnapshot(
            identity=identity,
            revision=revision,
            created_at_ms=int(time.time() * 1000),
            files=tuple(files),
            lineage=tuple(lineage),
            stale_paths=tuple(sorted(stale_paths)),
            deleted_paths=deleted,
            overflowed=overflowed,
        )

    def _walk_files(self, *, use_git_ignore: bool) -> Iterator[Path]:
        candidate_paths: list[Path] = []
        candidate_relatives: list[str] = []
        candidate_count = 0
        directories_seen = 0
        entries_seen = 0

        def flush_candidates() -> tuple[Path, ...]:
            if not candidate_paths:
                return ()
            ignored = self._git_ignored_paths(tuple(candidate_relatives)) if use_git_ignore else frozenset()
            allowed = tuple(
                path
                for path, relative in zip(candidate_paths, candidate_relatives, strict=True)
                if relative not in ignored
            )
            candidate_paths.clear()
            candidate_relatives.clear()
            return allowed

        directories_to_visit = [self.root]
        while directories_to_visit:
            directory = directories_to_visit.pop()
            directories_seen += 1
            if directories_seen > MAX_DIRECTORIES:
                yield from flush_candidates()
                raise _SourceTraversalLimit
            entries: list[_DirectoryEntry] = []
            entry_limit_reached = False
            try:
                with _scandir_directory(directory) as iterator:
                    for entry in iterator:
                        entries_seen += 1
                        if entries_seen > MAX_TRAVERSAL_ENTRIES:
                            entry_limit_reached = True
                            break
                        entries.append(entry)
            except OSError as error:
                yield from flush_candidates()
                raise _SourceTraversalLimit from error
            if entry_limit_reached:
                yield from flush_candidates()
                if not use_git_ignore:
                    raise CodeIntelligenceError("source ignore policy could not be evaluated")
                raise _SourceTraversalLimit
            entries.sort(key=lambda entry: entry.name)
            dirnames: list[str] = []
            filenames: list[str] = []
            for entry in entries:
                try:
                    entry_stat = entry.stat(follow_symlinks=False)
                except OSError as error:
                    if isinstance(error, FileNotFoundError):
                        is_directory = False
                    else:
                        yield from flush_candidates()
                        raise _SourceTraversalLimit from error
                else:
                    reparse_attribute = getattr(stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                    is_reparse_point = stat_module.S_ISLNK(entry_stat.st_mode) or bool(
                        getattr(entry_stat, "st_file_attributes", 0) & reparse_attribute
                    )
                    if is_reparse_point:
                        continue
                    is_directory = stat_module.S_ISDIR(entry_stat.st_mode)
                (dirnames if is_directory else filenames).append(entry.name)
            if not use_git_ignore and ".gitignore" in filenames:
                raise CodeIntelligenceError("source ignore policy requires a Git worktree")
            dirnames[:] = [name for name in dirnames if name.casefold() not in IGNORED_DIRECTORIES]
            directory_limit_reached = directories_seen + len(dirnames) > MAX_DIRECTORIES
            if directory_limit_reached and use_git_ignore:
                relative_directories = tuple((directory / name).relative_to(self.root).as_posix() for name in dirnames)
                ignored_directories: set[str] = set()
                for start in range(0, len(relative_directories), _IGNORE_CHECK_BATCH_SIZE):
                    ignored_directories.update(
                        self._git_ignored_paths(relative_directories[start : start + _IGNORE_CHECK_BATCH_SIZE])
                    )
                if ignored_directories:
                    dirnames[:] = [
                        name
                        for name, relative in zip(dirnames, relative_directories, strict=True)
                        if relative not in ignored_directories
                    ]
                    directory_limit_reached = directories_seen + len(dirnames) > MAX_DIRECTORIES
            if directory_limit_reached:
                dirnames.clear()
            for name in filenames:
                path = Path(directory) / name
                relative = path.relative_to(self.root).as_posix()
                if not _is_source_path(relative):
                    continue
                candidate_count += 1
                if candidate_count > self.max_files + _IGNORE_CHECK_BATCH_SIZE:
                    yield from flush_candidates()
                    raise _SourceTraversalLimit
                candidate_paths.append(path)
                candidate_relatives.append(relative)
                if len(candidate_paths) >= _IGNORE_CHECK_BATCH_SIZE:
                    yield from flush_candidates()
            if directory_limit_reached:
                yield from flush_candidates()
                raise _SourceTraversalLimit
            directories_to_visit.extend(directory / name for name in reversed(dirnames))
        yield from flush_candidates()

    def _git_ignored_paths(self, relative_paths: tuple[str, ...]) -> frozenset[str]:
        if not relative_paths:
            return frozenset()
        if len(relative_paths) > MAX_GIT_METADATA_PATHS:
            raise CodeIntelligenceError("source ignore policy exceeds the resource limit")
        encoded_paths: list[bytes] = []
        input_bytes = 0
        for path in relative_paths:
            encoded_path = os.fsencode(path)
            input_bytes += len(encoded_path) + 1
            if input_bytes > MAX_GIT_METADATA_BYTES:
                raise CodeIntelligenceError("source ignore policy exceeds the resource limit")
            encoded_paths.append(encoded_path)
        try:
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.root),
                    "check-ignore",
                    "--stdin",
                    "-z",
                    "--no-index",
                ],
                input=b"\0".join(encoded_paths) + b"\0",
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise CodeIntelligenceError("source ignore policy could not be evaluated") from error
        if result.returncode not in {0, 1}:
            raise CodeIntelligenceError("source ignore policy could not be evaluated")
        fields = result.stdout.split(b"\0")
        if result.stdout and not result.stdout.endswith(b"\0"):
            raise CodeIntelligenceError("source ignore policy returned invalid results")
        if fields and not fields[-1]:
            fields.pop()
        if len(fields) > len(relative_paths):
            raise CodeIntelligenceError("source ignore policy returned invalid results")
        return frozenset(os.fsdecode(path) for path in fields if path)

    def _extract(
        self,
        path: Path,
        relative: str,
        total_bytes: int,
        *,
        previous: SourceFileRecord | None = None,
        accounting: _SourceReadAccounting,
    ) -> SourceFileRecord | None:
        remaining_bytes = self.max_total_bytes - total_bytes
        read_limit = min(self.max_file_bytes, remaining_bytes)
        try:
            file_stat, raw = read_file_beneath_root(self.root, relative, max_bytes=read_limit)
        except SourceFileReadError as error:
            accounting.bytes_read = error.bytes_read
            if error.reason == "unsafe_path":
                return None
            failure_stat = error.file_stat if error.reason in {"too_large", "unsupported"} else None
            status = {
                "stat_error": "STAT_ERROR",
                "too_large": "SIZE_LIMIT",
                "unsupported": "UNSUPPORTED_FILE_TYPE",
            }.get(error.reason, "READ_ERROR")
            message = {
                "stat_error": "source metadata unavailable",
                "changed": "source changed during read",
            }.get(error.reason, "source content unavailable")
            return SourceFileRecord(
                relative,
                _language(path),
                failure_stat.st_size if failure_stat is not None else 0,
                failure_stat.st_mtime_ns if failure_stat is not None else 0,
                None,
                status,
                error=message,
            )
        accounting.bytes_read = len(raw)
        digest = hashlib.sha256(raw).hexdigest()
        language = _language(path)
        if previous is not None and previous.content_hash == digest and previous.language == language:
            return SourceFileRecord(
                relative,
                language,
                file_stat.st_size,
                file_stat.st_mtime_ns,
                digest,
                previous.extraction_status,
                previous.symbols,
                previous.imports,
                previous.error,
            )
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return SourceFileRecord(
                relative, _language(path), file_stat.st_size, file_stat.st_mtime_ns, digest, "BINARY_OR_INVALID_UTF8"
            )
        symbols, imports, status, error = _extract_text(language, text)
        return SourceFileRecord(
            relative,
            language,
            file_stat.st_size,
            file_stat.st_mtime_ns,
            digest,
            status,
            tuple(symbols[:MAX_SYMBOLS_PER_FILE]),
            tuple(imports[:MAX_IMPORTS_PER_FILE]),
            error,
        )


@dataclass(frozen=True)
class _GitFacts:
    repository_root: str | None
    common_git_dir: str | None
    checkout_path: str
    branch: str | None
    head: str | None
    dirty_paths: tuple[str, ...]


def _git_identity(root: Path) -> _GitFacts:
    checkout_path = str(root)
    try:
        top = _git(root, "rev-parse", "--show-toplevel")
        common = _git(root, "rev-parse", "--git-common-dir")
        branch = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD") or None
        try:
            head = _git(root, "rev-parse", "HEAD") or None
        except subprocess.CalledProcessError:
            if branch is None:
                raise
            head = None
        status = _git_status_output(root)
    except OSError, subprocess.SubprocessError:
        return _GitFacts(None, None, checkout_path, None, None, ())
    repository_root = Path(top).resolve()
    try:
        relative_root = root.resolve().relative_to(repository_root).as_posix()
    except ValueError:
        return _GitFacts(None, None, checkout_path, None, None, ())
    scope_prefix = b"" if relative_root in {".", ""} else os.fsencode(relative_root.rstrip("/")) + b"/"
    dirty_paths: set[str] = set()
    index = 0
    while index < len(status):
        boundary = status.find(b"\0", index)
        if boundary < 0:
            entry = status[index:]
            index = len(status)
        else:
            entry = status[index:boundary]
            index = boundary + 1
        if not entry:
            continue
        if len(entry) < 4 or entry[2:3] != b" ":
            return _GitFacts(None, None, checkout_path, None, None, ())
        status_code, raw_path = entry[:2], entry[3:]
        if not scope_prefix or raw_path.startswith(scope_prefix):
            scoped_path = raw_path[len(scope_prefix) :] if scope_prefix else raw_path
            dirty_paths.add(os.fsdecode(scoped_path))
            if len(dirty_paths) > MAX_GIT_METADATA_PATHS:
                raise CodeIntelligenceError("Git dirty metadata exceeds the resource limit")
        if b"R" in status_code or b"C" in status_code:
            if index >= len(status):
                return _GitFacts(None, None, checkout_path, None, None, ())
            boundary = status.find(b"\0", index)
            if boundary < 0:
                original_path = status[index:]
                index = len(status)
            else:
                original_path = status[index:boundary]
                index = boundary + 1
            if not scope_prefix or original_path.startswith(scope_prefix):
                scoped_original = original_path[len(scope_prefix) :] if scope_prefix else original_path
                dirty_paths.add(os.fsdecode(scoped_original))
                if len(dirty_paths) > MAX_GIT_METADATA_PATHS:
                    raise CodeIntelligenceError("Git dirty metadata exceeds the resource limit")
    return _GitFacts(
        str(repository_root),
        str((root / common).resolve()),
        checkout_path,
        branch,
        head,
        tuple(sorted(dirty_paths)),
    )


def _git_status_output(root: Path) -> bytes:
    command = [
        "git",
        "-C",
        str(root),
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "-z",
        "--",
        ".",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    output = process.stdout
    if output is None:
        raise subprocess.SubprocessError("Git status output pipe is unavailable")
    chunks: list[bytes] = []
    output_size = 0
    output_exceeded = threading.Event()
    reader_errors: list[OSError] = []

    def read_output() -> None:
        nonlocal output_size
        try:
            while chunk := output.read(64 * 1024):
                if output_size + len(chunk) > MAX_GIT_METADATA_BYTES:
                    output_exceeded.set()
                    with suppress(OSError):
                        process.kill()
                    return
                chunks.append(chunk)
                output_size += len(chunk)
        except OSError as error:
            reader_errors.append(error)
            with suppress(OSError):
                process.kill()

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()

    def stop_process() -> None:
        with suppress(OSError):
            process.kill()
        process.wait()

    try:
        try:
            return_code = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            stop_process()
            reader.join(timeout=5)
            raise
        reader.join(timeout=5)
        if reader.is_alive():
            stop_process()
            reader.join(timeout=5)
            raise subprocess.SubprocessError("Git status output reader did not stop")
        if output_exceeded.is_set():
            raise CodeIntelligenceError("Git dirty metadata exceeds the resource limit")
        if reader_errors:
            raise reader_errors[0]
        if return_code != 0:
            raise subprocess.CalledProcessError(return_code, command)
        return b"".join(chunks)
    finally:
        if process.poll() is None:
            stop_process()
        if not reader.is_alive():
            output.close()


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return completed.stdout.rstrip()


def _relative_event_path(root: Path, raw_path: str) -> str | None:
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        return candidate.absolute().relative_to(root).as_posix()
    except ValueError:
        return None


def _language(path: Path) -> str:
    return _SOURCE_LANGUAGES.get(path.suffix.lower(), "text")


def _is_source_path(relative_path: str) -> bool:
    path = PurePosixPath(relative_path)
    if not path.parts:
        return False
    if any(part.casefold() in IGNORED_DIRECTORIES for part in path.parts[:-1]):
        return False
    name = path.name.casefold()
    if name == ".env" or name.startswith(".env."):
        return False
    return path.suffix.casefold() in _SOURCE_LANGUAGES


def _normalized_source_path(relative_path: str) -> str:
    return os.path.normcase(PurePosixPath(relative_path).as_posix()).replace("\\", "/")


def _extract_text(language: str, text: str) -> tuple[list[SymbolRecord], list[str], str, str | None]:
    if language == "python":
        return _extract_python(text)
    if language == "rust":
        return _extract_pattern_language("rust", text)
    if language == "typescript":
        return _extract_pattern_language("typescript", text)
    if language == "javascript":
        return _extract_pattern_language("javascript", text)
    return [], [], "FALLBACK_HASH_ONLY", None


def _extract_python(text: str) -> tuple[list[SymbolRecord], list[str], str, str | None]:
    try:
        tree = ast.parse(text)
    except SyntaxError as error:
        return [], [], "SYNTAX_ERROR", f"line {error.lineno or 0}: {error.msg}"
    symbols: list[SymbolRecord] = []
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            signature = ast.dump(node.args, annotate_fields=True) if hasattr(node, "args") else node.name
            symbols.append(SymbolRecord(node.name, kind, int(node.lineno), _digest("aegis-symbol-v1", signature)))
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
                imports.extend(
                    f"{node.module}.{alias.name}" for alias in node.names if alias.name and alias.name != "*"
                )
            elif node.level > 0:
                imports.extend(alias.name for alias in node.names if alias.name and alias.name != "*")
    return symbols, sorted(set(imports)), "EXTRACTED", None


_PATTERNS: Mapping[str, tuple[tuple[str, str], ...]] = {
    "rust": (
        ("function", r"\bfn\s+([A-Za-z_][A-Za-z0-9_]*)"),
        ("struct", r"\bstruct\s+([A-Za-z_][A-Za-z0-9_]*)"),
        ("enum", r"\benum\s+([A-Za-z_][A-Za-z0-9_]*)"),
        ("trait", r"\btrait\s+([A-Za-z_][A-Za-z0-9_]*)"),
        ("module", r"\bmod\s+([A-Za-z_][A-Za-z0-9_]*)"),
    ),
    "typescript": (
        ("function", r"\bfunction\s+([A-Za-z_$][A-Za-z0-9_$]*)"),
        ("class", r"\bclass\s+([A-Za-z_$][A-Za-z0-9_$]*)"),
        ("interface", r"\binterface\s+([A-Za-z_$][A-Za-z0-9_$]*)"),
        ("type", r"\btype\s+([A-Za-z_$][A-Za-z0-9_$]*)"),
        ("variable", r"\b(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)"),
    ),
    "javascript": (
        ("function", r"\bfunction\s+([A-Za-z_$][A-Za-z0-9_$]*)"),
        ("class", r"\bclass\s+([A-Za-z_$][A-Za-z0-9_$]*)"),
        ("variable", r"\b(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)"),
    ),
}


def _extract_pattern_language(language: str, text: str) -> tuple[list[SymbolRecord], list[str], str, str | None]:
    symbols: list[SymbolRecord] = []
    code = _mask_comments_and_strings(text)
    for kind, pattern in _PATTERNS[language]:
        for match in re.finditer(pattern, code):
            line = code.count("\n", 0, match.start()) + 1
            line_end = text.find("\n", match.start())
            signature = text[match.start() : line_end if line_end >= 0 else len(text)]
            symbols.append(SymbolRecord(match.group(1), kind, line, _digest("aegis-symbol-v1", signature)))
    imports = re.findall(r"\b(?:use|import|from)\s+[^\s;\"']*([A-Za-z_][A-Za-z0-9_./:-]*)", code)
    return symbols, sorted(set(imports)), "LEXICAL_CANDIDATE", None


def _mask_comments_and_strings(text: str) -> str:
    """Keep line positions while removing lexical false positives."""

    chars = list(text)
    index = 0
    quote: str | None = None
    while index < len(chars):
        current = chars[index]
        following = chars[index + 1] if index + 1 < len(chars) else ""
        if quote is not None:
            if current == "\\":
                if current != "\n":
                    chars[index] = " "
                if index + 1 < len(chars) and chars[index + 1] != "\n":
                    chars[index + 1] = " "
                    index += 1
            elif current == quote:
                chars[index] = " "
                quote = None
            elif current != "\n":
                chars[index] = " "
            index += 1
            continue
        if current in {"'", '"', "`"}:
            chars[index] = " "
            quote = current
            index += 1
            continue
        if current == "/" and following == "/":
            chars[index] = chars[index + 1] = " "
            index += 2
            while index < len(chars) and chars[index] != "\n":
                chars[index] = " "
                index += 1
            continue
        if current == "/" and following == "*":
            chars[index] = chars[index + 1] = " "
            index += 2
            while index < len(chars):
                if chars[index] == "*" and index + 1 < len(chars) and chars[index + 1] == "/":
                    chars[index] = chars[index + 1] = " "
                    index += 2
                    break
                if chars[index] != "\n":
                    chars[index] = " "
                index += 1
            continue
        index += 1
    return "".join(chars)


def _build_lineage(files: Iterable[SourceFileRecord]) -> list[LineageCandidate]:
    records = tuple(files)
    by_stem: dict[str, list[str]] = {}
    by_signature: dict[str, list[tuple[str, str]]] = {}
    for record in records:
        by_stem.setdefault(Path(record.relative_path).stem.casefold(), []).append(record.relative_path)
        for symbol in record.symbols:
            by_signature.setdefault(symbol.signature_hash, []).append((record.relative_path, symbol.name))
    sorted_by_stem: dict[str, tuple[str, ...]] = {stem: tuple(sorted(paths)) for stem, paths in by_stem.items()}
    sorted_by_signature: dict[str, tuple[tuple[str, str], ...]] = {
        sig: tuple(sorted(items, key=lambda item: item[0])) for sig, items in by_signature.items()
    }
    candidates: list[LineageCandidate] = []
    for record in records:
        rel_path = record.relative_path
        for symbol in record.symbols:
            stem_bucket = sorted_by_stem.get(symbol.name.casefold(), ())
            paths = tuple(path for path in stem_bucket if path != rel_path) if stem_bucket else ()
            if paths:
                candidates.append(LineageCandidate(rel_path, symbol.name, paths, "duplicate-or-name-match"))
            sig_bucket = sorted_by_signature.get(symbol.signature_hash, ())
            signature_matches = (
                tuple(path for path, name in sig_bucket if path != rel_path and name == symbol.name)
                if sig_bucket
                else ()
            )
            if signature_matches and not paths:
                candidates.append(LineageCandidate(rel_path, symbol.name, signature_matches, "duplicate-signature"))
        for imported in record.imports:
            stem = Path(imported.replace(".", "/")).stem.casefold()
            stem_bucket = sorted_by_stem.get(stem, ())
            paths = tuple(path for path in stem_bucket if path != rel_path) if stem_bucket else ()
            if paths:
                candidates.append(LineageCandidate(rel_path, imported, paths, "import-path-match"))
    return candidates


def compare_snapshots(previous: SourceSnapshot, current: SourceSnapshot) -> SnapshotDelta:
    old = {item.relative_path: item for item in previous.files}
    new = {item.relative_path: item for item in current.files}
    added = {path for path in new if path not in old}
    deleted = {path for path in old if path not in new}
    changed = {path for path in new if path in old and new[path].content_hash != old[path].content_hash}
    old_by_hash: dict[str, list[str]] = {}
    new_by_hash: dict[str, list[str]] = {}
    for path, record in old.items():
        if record.content_hash:
            old_by_hash.setdefault(record.content_hash, []).append(path)
    for path, record in new.items():
        if record.content_hash:
            new_by_hash.setdefault(record.content_hash, []).append(path)
    renamed: set[tuple[str, str]] = set()
    copied: set[tuple[str, str]] = set()
    for digest, old_paths in old_by_hash.items():
        new_paths = new_by_hash.get(digest, [])
        for old_path in old_paths:
            for new_path in new_paths:
                if new_path in old:
                    continue
                (renamed if len(old_paths) == 1 and len(new_paths) == 1 else copied).add((old_path, new_path))
    renamed_old = {old_path for old_path, _ in renamed}
    renamed_new = {new_path for _, new_path in renamed}
    return SnapshotDelta(
        tuple(sorted(added - renamed_new)),
        tuple(sorted(changed)),
        tuple(sorted(deleted - renamed_old)),
        tuple(sorted(renamed)),
        tuple(sorted(copied)),
    )


def invalidate_dependencies(snapshot: SourceSnapshot, changed_paths: Iterable[str]) -> tuple[str, ...]:
    def normalize(path: str) -> str:
        return Path(path).as_posix().casefold()

    changed_display = {normalize(path): Path(path).as_posix() for path in changed_paths}
    affected: dict[str, str] = dict(changed_display)
    reverse: dict[str, dict[str, str]] = {}
    for candidate in snapshot.lineage:
        for path in candidate.candidate_paths:
            reverse.setdefault(normalize(path), {})[normalize(candidate.source_path)] = candidate.source_path
    pending = list(changed_display)
    while pending:
        dependency = pending.pop()
        for normalized, source_path in reverse.get(dependency, {}).items():
            if normalized not in affected:
                affected[normalized] = source_path
                pending.append(normalized)
    return tuple(sorted(affected.values()))


def build_repository_map(
    snapshot: SourceSnapshot,
    *,
    query: str = "",
    token_budget: int = 2_048,
    max_files: int = MAX_REPO_MAP_FILES,
) -> RepositoryMap:
    """Project one source revision into a bounded symbol/import map.

    The map contains no source text. It is a context hint only: exact source
    hydration must still go through an authorized ``SourceSnapshot`` path.
    Ranking is deliberately deterministic and lexical so this function does
    not introduce a second index, embedding service, or provider dependency.
    """

    if type(snapshot) is not SourceSnapshot:
        raise CodeIntelligenceError("repository map requires a SourceSnapshot")
    if not isinstance(query, str) or len(query) > MAX_REPO_MAP_QUERY_LENGTH:
        raise ValueError("repository map query must be a bounded string")
    if isinstance(token_budget, bool) or not isinstance(token_budget, int) or token_budget < 1:
        raise ValueError("repository map token_budget must be a positive integer")
    if isinstance(max_files, bool) or not isinstance(max_files, int) or not 1 <= max_files <= MAX_REPO_MAP_FILES:
        raise ValueError(f"repository map max_files must be between 1 and {MAX_REPO_MAP_FILES}")

    normalized_query = query.strip()
    records = tuple(sorted(snapshot.files, key=lambda item: item.relative_path))
    terms = _repository_map_terms(normalized_query)
    scored_records = tuple(
        sorted(
            ((_repository_map_score(record, terms), record) for record in records),
            key=lambda item: (-item[0], item[1].relative_path),
        )
    )
    total_files = len(records)
    base_envelope_utf8_len = len(
        "\n".join(
            (
                "[AEGIS REPOSITORY MAP — SOURCE BOUND]",
                f"source_revision: {snapshot.revision}",
                f"query: {normalized_query or '(none)'}",
                f"source_files_total: {total_files}",
                "source_files_selected: ",
                "source_files_omitted: ",
                f"token_budget: {token_budget}",
                "selection: deterministic lexical path/symbol/import heuristic; not dependency proof",
                "[/AEGIS REPOSITORY MAP]",
            )
        ).encode("utf-8")
    )
    selected: list[RepositoryMapEntry] = []
    selected_blocks: list[str] = []
    selected_blocks_utf8_len = 0
    for score, record in scored_records:
        if len(selected) >= max_files:
            break
        entry, entry_block = _repository_map_entry_with_block(record, score)
        entry_utf8_len = len(entry_block.encode("utf-8")) + 1
        trial_count = len(selected) + 1
        trial_omitted = max(0, total_files - trial_count)
        trial_utf8_len = (
            base_envelope_utf8_len
            + len(str(trial_count))
            + len(str(trial_omitted))
            + selected_blocks_utf8_len
            + entry_utf8_len
        )
        if _estimate_repository_map_tokens_from_utf8_len(trial_utf8_len) <= token_budget:
            selected.append(entry)
            selected_blocks.append(entry_block)
            selected_blocks_utf8_len += entry_utf8_len

    rendered = _render_repository_map(
        snapshot,
        normalized_query,
        token_budget,
        selected=selected,
        total_files=total_files,
        rendered_entries=selected_blocks,
    )
    token_count = _estimate_repository_map_tokens(rendered)
    if token_count > token_budget:
        raise CodeIntelligenceError("repository map budget is too small for its bounded envelope")
    omitted_files = max(0, total_files - len(selected))
    complete = not snapshot.overflowed and omitted_files == 0
    map_hash = _digest(
        "aegis-repository-map-v1",
        snapshot.revision,
        normalized_query,
        str(token_budget),
        rendered,
    )
    return RepositoryMap(
        schema="aegis-repository-map-v1",
        source_revision=snapshot.revision,
        query=normalized_query,
        token_budget=token_budget,
        token_count=token_count,
        accounting="estimated-byte-heuristic-v1",
        complete=complete,
        entries=tuple(selected),
        omitted_files=omitted_files,
        rendered=rendered,
        map_hash=map_hash,
    )


_REPOSITORY_MAP_STOP_WORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "project",
        "file",
        "code",
        "show",
        "what",
        "how",
        "please",
    }
)


def _repository_map_terms(query: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            {term for term in re.findall(r"[a-z0-9_]{3,}", query.casefold()) if term not in _REPOSITORY_MAP_STOP_WORDS}
        )
    )


def _repository_map_score(record: SourceFileRecord, terms: tuple[str, ...]) -> int:
    path = record.relative_path.casefold()
    symbols = " ".join(symbol.name for symbol in record.symbols).casefold()
    imports = " ".join(record.imports).casefold()
    return (
        sum(term in path for term in terms) * 8
        + sum(term in symbols for term in terms) * 5
        + sum(term in imports for term in terms) * 2
        + min(len(record.symbols), 8)
    )


def _repository_map_entry_with_block(record: SourceFileRecord, score: int) -> tuple[RepositoryMapEntry, str]:
    symbols = tuple(sorted(record.symbols, key=lambda item: (item.line, item.kind, item.name)))[
        :MAX_REPO_MAP_SYMBOLS_PER_FILE
    ]
    imports = tuple(sorted(record.imports))[:MAX_REPO_MAP_IMPORTS_PER_FILE]
    content = _render_repository_map_entry(record, score, symbols, imports)
    entry = RepositoryMapEntry(
        relative_path=record.relative_path,
        language=record.language,
        size_bytes=record.size_bytes,
        content_hash=record.content_hash,
        extraction_status=record.extraction_status,
        score=score,
        symbols=symbols[:MAX_REPO_MAP_SYMBOLS_PER_FILE],
        imports=imports[:MAX_REPO_MAP_IMPORTS_PER_FILE],
        token_cost=_estimate_repository_map_tokens(content),
    )
    return entry, content


def _repository_map_entry(record: SourceFileRecord, score: int) -> RepositoryMapEntry:
    entry, _ = _repository_map_entry_with_block(record, score)
    return entry


def _render_repository_map_entry(
    record: SourceFileRecord,
    score: int,
    symbols: tuple[SymbolRecord, ...],
    imports: tuple[str, ...],
) -> str:
    symbol_text = ", ".join(
        f"{item.kind} {item.name}@{item.line}#{item.signature_hash[:12]}"
        for item in symbols[:MAX_REPO_MAP_SYMBOLS_PER_FILE]
    )
    import_text = ", ".join(imports[:MAX_REPO_MAP_IMPORTS_PER_FILE])
    return "\n".join(
        (
            f"[FILE path={record.relative_path} language={record.language} status={record.extraction_status} score={score}]",
            f"size_bytes: {record.size_bytes}",
            f"content_hash: {record.content_hash or '(none)'}",
            f"symbols: {symbol_text or '(none)'}",
            f"imports: {import_text or '(none)'}",
            "[/FILE]",
        )
    )


def _render_repository_map(
    snapshot: SourceSnapshot,
    query: str,
    token_budget: int,
    *,
    selected: list[RepositoryMapEntry],
    total_files: int,
    rendered_entries: Sequence[str] | None = None,
) -> str:
    blocks = [
        "[AEGIS REPOSITORY MAP — SOURCE BOUND]",
        f"source_revision: {snapshot.revision}",
        f"query: {query or '(none)'}",
        f"source_files_total: {total_files}",
        f"source_files_selected: {len(selected)}",
        f"source_files_omitted: {max(0, total_files - len(selected))}",
        f"token_budget: {token_budget}",
        "selection: deterministic lexical path/symbol/import heuristic; not dependency proof",
    ]
    if rendered_entries is not None and len(rendered_entries) == len(selected):
        blocks.extend(rendered_entries)
    else:
        blocks.extend(
            _render_repository_map_entry(
                SourceFileRecord(
                    entry.relative_path,
                    entry.language,
                    entry.size_bytes,
                    0,
                    entry.content_hash,
                    entry.extraction_status,
                    entry.symbols,
                    entry.imports,
                ),
                entry.score,
                entry.symbols,
                entry.imports,
            )
            for entry in selected
        )
    blocks.append("[/AEGIS REPOSITORY MAP]")
    return "\n".join(blocks)


def _estimate_repository_map_tokens_from_utf8_len(utf8_len: int) -> int:
    return max(1, (utf8_len + 3) // 4) + 8


def _estimate_repository_map_tokens(text: str) -> int:
    return _estimate_repository_map_tokens_from_utf8_len(len(text.encode("utf-8")))


def _digest(prefix: str, *parts: str) -> str:
    hasher = hashlib.sha256()
    hasher.update(prefix.encode())
    for part in parts:
        encoded = part.encode("utf-8")
        hasher.update(len(encoded).to_bytes(8, "big"))
        hasher.update(encoded)
    return hasher.hexdigest()


def _snapshot_revision(
    identity: ProjectIdentity, files: Iterable[SourceFileRecord], lineage: Iterable[LineageCandidate]
) -> str:
    parts = [identity.project_id, identity.checkout_id]
    parts.extend(f"{record.relative_path}|{record.content_hash or ''}|{record.extraction_status}" for record in files)
    parts.extend(f"{item.source_path}|{item.symbol}|{','.join(item.candidate_paths)}|{item.reason}" for item in lineage)
    return _digest("aegis-source-snapshot-v1", *parts)


__all__ = [
    "IGNORED_DIRECTORIES",
    "MAX_DIRECTORIES",
    "MAX_FILES",
    "MAX_FILE_BYTES",
    "MAX_REPO_MAP_FILES",
    "MAX_REPO_MAP_IMPORTS_PER_FILE",
    "MAX_REPO_MAP_SYMBOLS_PER_FILE",
    "MAX_TOTAL_BYTES",
    "MAX_WATCH_EVENTS",
    "BoundedSnapshotWatcher",
    "CodeIntelligenceError",
    "LineageCandidate",
    "NativeSnapshotWatcher",
    "ProjectIdentity",
    "RepositoryMap",
    "RepositoryMapEntry",
    "SnapshotDelta",
    "SourceFileRecord",
    "SourceMapper",
    "SourceSnapshot",
    "SymbolRecord",
    "WatchEvent",
    "build_repository_map",
    "compare_snapshots",
    "invalidate_dependencies",
]
