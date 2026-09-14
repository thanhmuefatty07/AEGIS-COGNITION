"""Generic bounded adapter seam for the first AESE vertical slice."""

from __future__ import annotations

import re
from dataclasses import dataclass
from collections.abc import Mapping
from typing import cast


class AdapterFailure(ValueError):
    """Raised when an adapter input cannot be represented safely."""


@dataclass(frozen=True)
class AdapterCapabilities:
    adapter: str
    level: str
    structured_discovery: bool
    structured_results: bool
    impact_aware_planning: bool
    quality_assessment: bool


@dataclass(frozen=True)
class BoundedCommand:
    executable: str
    argv: tuple[str, ...]
    working_directory: str
    timeout_seconds: float
    environment: Mapping[str, str]

    def validate(self) -> None:
        if not self.executable.strip() or "\x00" in self.executable:
            raise AdapterFailure("executable must be a non-empty path")
        if any(type(argument) is not str or "\x00" in argument for argument in self.argv):
            raise AdapterFailure("argv must contain bounded strings")
        if not self.working_directory.strip() or "\x00" in self.working_directory:
            raise AdapterFailure("working_directory must be a non-empty path")
        if type(self.timeout_seconds) not in (int, float) or self.timeout_seconds <= 0:
            raise AdapterFailure("timeout_seconds must be positive")
        if not isinstance(cast(object, self.environment), Mapping) or any(
            type(key) is not str or type(value) is not str for key, value in self.environment.items()
        ):
            raise AdapterFailure("environment must be a string mapping")


@dataclass(frozen=True)
class ParsedTestResult:
    status: str
    discovered: int
    passed: int
    failed: int
    skipped: int
    filtered: int
    ignored: int
    failure_class: str | None = None
    detail: str = ""

    def validate(self) -> None:
        if self.status not in {"PASS", "FAIL", "ZERO_TESTS", "MALFORMED", "ERROR"}:
            raise AdapterFailure("unknown parsed result status")
        counts = (self.discovered, self.passed, self.failed, self.skipped, self.filtered, self.ignored)
        if any(type(value) is not int or value < 0 for value in counts):
            raise AdapterFailure("test result counts must be non-negative integers")
        if self.passed + self.failed + self.skipped + self.filtered + self.ignored > self.discovered:
            raise AdapterFailure("test result counts exceed discovered tests")
        if not self.detail.strip():
            raise AdapterFailure("parsed result requires a detail")


class GenericBoundedAdapter:
    """Safe command/result adapter; it never invokes a shell or subprocess."""

    name = "custom"

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            adapter=self.name,
            level="L1",
            structured_discovery=False,
            structured_results=True,
            impact_aware_planning=False,
            quality_assessment=False,
        )

    def detect(self, project_files: tuple[str, ...] | list[str]) -> bool:
        return bool(project_files)

    def validate_environment(self, *, executable: str, version: str | None = None) -> tuple[bool, str]:
        if not executable.strip() or "\x00" in executable:
            return False, "executable_invalid"
        if version is not None and (not version.strip() or "\x00" in version):
            return False, "version_invalid"
        return True, "environment_declared"

    def discover_tests(self, test_paths: tuple[str, ...] | list[str]) -> tuple[str, ...]:
        return tuple(sorted(path for path in test_paths if path.strip()))

    def build_command(
        self,
        *,
        executable: str,
        argv: tuple[str, ...] | list[str],
        working_directory: str,
        timeout_seconds: float,
        environment: Mapping[str, str] = {},
    ) -> BoundedCommand:
        command = BoundedCommand(
            executable=executable,
            argv=tuple(argv),
            working_directory=working_directory,
            timeout_seconds=timeout_seconds,
            environment=environment,
        )
        command.validate()
        return command

    def parse_result(self, payload: object) -> ParsedTestResult:
        if not isinstance(payload, Mapping):
            return self._malformed("result payload is not a mapping")
        typed_payload = cast(Mapping[str, object], payload)
        status = typed_payload.get("status")
        raw_counts = {
            name: typed_payload.get(name, 0)
            for name in ("discovered", "passed", "failed", "skipped", "filtered", "ignored")
        }
        if type(status) is not str:
            return self._malformed("result status is missing")
        if any(type(value) is not int or value < 0 for value in raw_counts.values()):
            return self._malformed("result counts are malformed")
        counts = cast(dict[str, int], raw_counts)
        if counts["discovered"] == 0 and status == "PASS":
            return self._malformed("zero tests cannot be reported as PASS")
        normalized = status.upper()
        if normalized not in {"PASS", "FAIL", "ZERO_TESTS", "ERROR"}:
            return self._malformed("result status is unknown")
        parsed = ParsedTestResult(
            status=normalized,
            discovered=counts["discovered"],
            passed=counts["passed"],
            failed=counts["failed"],
            skipped=counts["skipped"],
            filtered=counts["filtered"],
            ignored=counts["ignored"],
            failure_class=str(typed_payload.get("failure_class"))
            if typed_payload.get("failure_class") is not None
            else None,
            detail=str(typed_payload.get("detail", "structured adapter result")),
        )
        try:
            parsed.validate()
        except AdapterFailure as error:
            return self._malformed(str(error))
        return parsed

    def classify_failure(
        self, *, exit_code: int | None, timed_out: bool = False, cancelled: bool = False, output: str = ""
    ) -> str:
        if cancelled:
            return "CANCELLATION"
        if timed_out:
            return "TIMEOUT"
        if exit_code is None:
            return "MALFORMED_RESULT"
        if exit_code == 0:
            return "NONE"
        if re.search(r"collection|discovery|compile|build", output, flags=re.IGNORECASE):
            return "COLLECTION_OR_BUILD_ERROR"
        return "TEST_FAILURE"

    @staticmethod
    def _malformed(detail: str) -> ParsedTestResult:
        return ParsedTestResult(
            status="MALFORMED",
            discovered=0,
            passed=0,
            failed=0,
            skipped=0,
            filtered=0,
            ignored=0,
            failure_class="MALFORMED_RESULT",
            detail=detail,
        )


__all__ = [
    "AdapterCapabilities",
    "AdapterFailure",
    "BoundedCommand",
    "GenericBoundedAdapter",
    "ParsedTestResult",
]
