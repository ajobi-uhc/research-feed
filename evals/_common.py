"""Shared eval helpers. Tiny on purpose."""

from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def load_profile(name: str):
    """Load a fixture profile by name (without .json suffix)."""
    from src.models import Profile
    path = FIXTURES / f"{name}.json"
    return Profile.from_dict(json.loads(path.read_text()))


@dataclass
class TestResult:
    name: str
    passed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def ok(self, msg: str) -> None:
        self.passed.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def fail(self, msg: str) -> None:
        self.failures.append(msg)

    @property
    def status(self) -> str:
        if self.failures:
            return "FAIL"
        if self.warnings:
            return "PASS (with warnings)"
        return "PASS"

    def print(self) -> None:
        print(f"\nTEST: {self.name}")
        for m in self.passed:
            print(f"  ✓ {m}")
        for m in self.warnings:
            print(f"  ⚠ {m}")
        for m in self.failures:
            print(f"  ✗ {m}")
        print(f"{self.status}")


def check(result: TestResult, cond: bool, ok_msg: str, fail_msg: str) -> None:
    """Convenience: pass or fail based on cond."""
    if cond:
        result.ok(ok_msg)
    else:
        result.fail(fail_msg)
