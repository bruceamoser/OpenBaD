"""eBPF probe scaffold for kernel-level resource monitoring.

Provides a :class:`ProbeManager` that can load and unload eBPF probes
attached to kernel tracepoints.  The initial probes target:

- ``sched_switch`` — CPU scheduler context switches (per-cgroup CPU time)
- ``mm_page_alloc`` — memory page allocations

Requires Linux kernel >= 5.8 and the ``bcc`` Python package (Apache 2.0).
On non-Linux systems, :class:`ProbeManager` raises :class:`ProbeError`
on any operation that requires the kernel.
"""

from __future__ import annotations

import logging
import platform
from dataclasses import dataclass, field
from enum import Enum, auto

logger = logging.getLogger(__name__)


class ProbeError(RuntimeError):
    """Raised when an eBPF probe operation fails."""


class ProbeState(Enum):
    """Lifecycle state of an eBPF probe."""

    UNLOADED = auto()
    LOADED = auto()
    ATTACHED = auto()
    ERROR = auto()


@dataclass
class ProbeSpec:
    """Specification for a single eBPF probe.

    Attributes:
        name: Human-readable probe identifier.
        tracepoint: Kernel tracepoint category and event
                    (e.g. ``"sched:sched_switch"``).
        program: BPF C source text to compile and attach.
    """

    name: str
    tracepoint: str
    program: str
    state: ProbeState = field(default=ProbeState.UNLOADED, init=False)


# ---------------------------------------------------------------------------
# Built-in probe programs (BPF C source)
# ---------------------------------------------------------------------------

SCHED_SWITCH_BPF = """\
#include <uapi/linux/ptrace.h>

BPF_HASH(cpu_time, u32, u64);

TRACEPOINT_PROBE(sched, sched_switch) {
    u32 pid = bpf_get_current_pid_tgid() >> 32;
    u64 ts = bpf_ktime_get_ns();
    cpu_time.update(&pid, &ts);
    return 0;
}
"""

MM_PAGE_ALLOC_BPF = """\
#include <uapi/linux/ptrace.h>

BPF_HASH(page_allocs, u32, u64);

TRACEPOINT_PROBE(kmem, mm_page_alloc) {
    u32 pid = bpf_get_current_pid_tgid() >> 32;
    u64 zero = 0;
    u64 *count = page_allocs.lookup_or_try_init(&pid, &zero);
    if (count) {
        (*count)++;
    }
    return 0;
}
"""

# Default probes shipped with OpenBaD
DEFAULT_PROBES: tuple[ProbeSpec, ...] = (
    ProbeSpec(
        name="cpu_sched_switch",
        tracepoint="sched:sched_switch",
        program=SCHED_SWITCH_BPF,
    ),
    ProbeSpec(
        name="mem_page_alloc",
        tracepoint="kmem:mm_page_alloc",
        program=MM_PAGE_ALLOC_BPF,
    ),
)


def _require_linux() -> None:
    if platform.system() != "Linux":
        msg = "eBPF probes require Linux kernel >= 5.8"
        raise ProbeError(msg)


def _import_bcc():  # noqa: ANN202
    """Lazily import bcc to avoid hard dependency."""
    try:
        from bcc import BPF  # type: ignore[import-untyped]
    except ImportError as exc:
        msg = (
            "The 'bcc' package is required for eBPF probes. "
            "Install it via your system package manager "
            "(e.g. apt install python3-bcc)."
        )
        raise ProbeError(msg) from exc
    return BPF


class ProbeManager:
    """Manages the lifecycle of eBPF probes.

    Usage::

        pm = ProbeManager()
        pm.load_defaults()     # loads cpu_sched_switch + mem_page_alloc
        pm.unload_all()

    On non-Linux systems, all mutating operations raise :class:`ProbeError`.
    Read-only inspection (``loaded_probes``, ``get_probe``) works anywhere.
    """

    def __init__(self) -> None:
        self._probes: dict[str, ProbeSpec] = {}
        self._bpf_instances: dict[str, object] = {}

    @property
    def loaded_probes(self) -> dict[str, ProbeState]:
        """Return a name→state mapping for all registered probes."""
        return {name: spec.state for name, spec in self._probes.items()}

    def get_probe(self, name: str) -> ProbeSpec | None:
        """Return the :class:`ProbeSpec` for *name*, or None."""
        return self._probes.get(name)

    def register(self, spec: ProbeSpec) -> None:
        """Register a probe spec without loading it."""
        spec.state = ProbeState.UNLOADED
        self._probes[spec.name] = spec
        logger.debug("Registered probe %s (tracepoint=%s)", spec.name, spec.tracepoint)

    def load(self, name: str) -> None:
        """Compile and attach the probe identified by *name*.

        Raises :class:`ProbeError` on non-Linux or if bcc is not installed.
        """
        _require_linux()
        spec = self._probes.get(name)
        if spec is None:
            msg = f"No probe registered with name '{name}'"
            raise ProbeError(msg)

        if spec.state == ProbeState.ATTACHED:
            logger.warning("Probe %s is already attached", name)
            return

        bpf_cls = _import_bcc()
        try:
            bpf = bpf_cls(text=spec.program)
            self._bpf_instances[name] = bpf
            spec.state = ProbeState.ATTACHED
            logger.info("Loaded and attached probe %s → %s", name, spec.tracepoint)
        except Exception as exc:
            spec.state = ProbeState.ERROR
            msg = f"Failed to load probe {name}: {exc}"
            raise ProbeError(msg) from exc

    def unload(self, name: str) -> None:
        """Detach and clean up the probe identified by *name*."""
        _require_linux()
        spec = self._probes.get(name)
        if spec is None:
            msg = f"No probe registered with name '{name}'"
            raise ProbeError(msg)

        bpf = self._bpf_instances.pop(name, None)
        if bpf is not None:
            try:
                bpf.cleanup()  # type: ignore[union-attr]
            except Exception:
                logger.exception("Error cleaning up probe %s", name)
        spec.state = ProbeState.UNLOADED
        logger.info("Unloaded probe %s", name)

    def load_defaults(self) -> None:
        """Register and load all :data:`DEFAULT_PROBES`."""
        for spec in DEFAULT_PROBES:
            self.register(spec)
        for spec in DEFAULT_PROBES:
            self.load(spec.name)

    def unload_all(self) -> None:
        """Unload every currently loaded probe."""
        _require_linux()
        for name in list(self._bpf_instances.keys()):
            self.unload(name)
        logger.info("All probes unloaded")
