"""Tests for openbad.interoception.ebpf_probes — eBPF probe manager.

All tests mock bcc and kernel interactions so they run on any OS.
Integration tests that require a real Linux kernel and bcc are marked
with ``@pytest.mark.integration``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from openbad.interoception.ebpf_probes import (
    DEFAULT_PROBES,
    MM_PAGE_ALLOC_BPF,
    SCHED_SWITCH_BPF,
    ProbeError,
    ProbeManager,
    ProbeSpec,
    ProbeState,
)

# ── ProbeSpec ──────────────────────────────────────────────────────


class TestProbeSpec:
    def test_default_state_is_unloaded(self):
        spec = ProbeSpec(name="test", tracepoint="sched:sched_switch", program="//noop")
        assert spec.state == ProbeState.UNLOADED

    def test_fields(self):
        spec = ProbeSpec(name="cpu", tracepoint="sched:sched_switch", program="code")
        assert spec.name == "cpu"
        assert spec.tracepoint == "sched:sched_switch"
        assert spec.program == "code"


# ── DEFAULT_PROBES ─────────────────────────────────────────────────


class TestDefaultProbes:
    def test_has_two_probes(self):
        assert len(DEFAULT_PROBES) == 2

    def test_sched_switch_probe(self):
        probe = DEFAULT_PROBES[0]
        assert probe.name == "cpu_sched_switch"
        assert probe.tracepoint == "sched:sched_switch"
        assert probe.program == SCHED_SWITCH_BPF

    def test_page_alloc_probe(self):
        probe = DEFAULT_PROBES[1]
        assert probe.name == "mem_page_alloc"
        assert probe.tracepoint == "kmem:mm_page_alloc"
        assert probe.program == MM_PAGE_ALLOC_BPF

    def test_bpf_programs_are_non_empty(self):
        assert len(SCHED_SWITCH_BPF) > 0
        assert len(MM_PAGE_ALLOC_BPF) > 0


# ── ProbeManager registration ─────────────────────────────────────


class TestProbeManagerRegistration:
    def test_register_and_get(self):
        pm = ProbeManager()
        spec = ProbeSpec(name="test", tracepoint="t:e", program="p")
        pm.register(spec)
        assert pm.get_probe("test") is spec

    def test_get_unknown_returns_none(self):
        pm = ProbeManager()
        assert pm.get_probe("nonexistent") is None

    def test_loaded_probes_after_register(self):
        pm = ProbeManager()
        spec = ProbeSpec(name="test", tracepoint="t:e", program="p")
        pm.register(spec)
        states = pm.loaded_probes
        assert states == {"test": ProbeState.UNLOADED}

    def test_register_overwrites(self):
        pm = ProbeManager()
        spec1 = ProbeSpec(name="test", tracepoint="t:e1", program="p1")
        spec2 = ProbeSpec(name="test", tracepoint="t:e2", program="p2")
        pm.register(spec1)
        pm.register(spec2)
        assert pm.get_probe("test") is spec2


# ── ProbeManager.load (mocked) ────────────────────────────────────


class TestProbeManagerLoad:
    @patch("openbad.interoception.ebpf_probes.platform")
    @patch("openbad.interoception.ebpf_probes._import_bcc")
    def test_load_attaches_probe(self, mock_import_bcc, mock_platform):
        mock_platform.system.return_value = "Linux"
        mock_bpf_cls = MagicMock()
        mock_import_bcc.return_value = mock_bpf_cls

        pm = ProbeManager()
        spec = ProbeSpec(name="test", tracepoint="t:e", program="test_prog")
        pm.register(spec)
        pm.load("test")

        mock_bpf_cls.assert_called_once_with(text="test_prog")
        assert spec.state == ProbeState.ATTACHED

    @patch("openbad.interoception.ebpf_probes.platform")
    def test_load_raises_on_non_linux(self, mock_platform):
        mock_platform.system.return_value = "Windows"
        pm = ProbeManager()
        pm.register(ProbeSpec(name="test", tracepoint="t:e", program="p"))
        with pytest.raises(ProbeError, match="require Linux"):
            pm.load("test")

    @patch("openbad.interoception.ebpf_probes.platform")
    def test_load_raises_for_unknown_probe(self, mock_platform):
        mock_platform.system.return_value = "Linux"
        pm = ProbeManager()
        with pytest.raises(ProbeError, match="No probe registered"):
            pm.load("ghost")

    @patch("openbad.interoception.ebpf_probes.platform")
    @patch("openbad.interoception.ebpf_probes._import_bcc")
    def test_load_already_attached_is_noop(self, mock_import_bcc, mock_platform):
        mock_platform.system.return_value = "Linux"
        mock_bpf_cls = MagicMock()
        mock_import_bcc.return_value = mock_bpf_cls

        pm = ProbeManager()
        spec = ProbeSpec(name="test", tracepoint="t:e", program="p")
        pm.register(spec)
        pm.load("test")
        # Second load should be a no-op
        pm.load("test")
        # BPF class only called once
        assert mock_bpf_cls.call_count == 1

    @patch("openbad.interoception.ebpf_probes.platform")
    @patch("openbad.interoception.ebpf_probes._import_bcc")
    def test_load_sets_error_on_failure(self, mock_import_bcc, mock_platform):
        mock_platform.system.return_value = "Linux"
        mock_bpf_cls = MagicMock(side_effect=RuntimeError("compile error"))
        mock_import_bcc.return_value = mock_bpf_cls

        pm = ProbeManager()
        spec = ProbeSpec(name="test", tracepoint="t:e", program="bad")
        pm.register(spec)
        with pytest.raises(ProbeError, match="Failed to load"):
            pm.load("test")
        assert spec.state == ProbeState.ERROR


# ── ProbeManager.unload (mocked) ──────────────────────────────────


class TestProbeManagerUnload:
    @patch("openbad.interoception.ebpf_probes.platform")
    @patch("openbad.interoception.ebpf_probes._import_bcc")
    def test_unload_cleans_up(self, mock_import_bcc, mock_platform):
        mock_platform.system.return_value = "Linux"
        mock_bpf = MagicMock()
        mock_bpf_cls = MagicMock(return_value=mock_bpf)
        mock_import_bcc.return_value = mock_bpf_cls

        pm = ProbeManager()
        spec = ProbeSpec(name="test", tracepoint="t:e", program="p")
        pm.register(spec)
        pm.load("test")
        pm.unload("test")

        mock_bpf.cleanup.assert_called_once()
        assert spec.state == ProbeState.UNLOADED

    @patch("openbad.interoception.ebpf_probes.platform")
    def test_unload_raises_for_unknown(self, mock_platform):
        mock_platform.system.return_value = "Linux"
        pm = ProbeManager()
        with pytest.raises(ProbeError, match="No probe registered"):
            pm.unload("ghost")

    @patch("openbad.interoception.ebpf_probes.platform")
    def test_unload_raises_on_non_linux(self, mock_platform):
        mock_platform.system.return_value = "Windows"
        pm = ProbeManager()
        pm.register(ProbeSpec(name="test", tracepoint="t:e", program="p"))
        with pytest.raises(ProbeError, match="require Linux"):
            pm.unload("test")


# ── ProbeManager.load_defaults ─────────────────────────────────────


class TestLoadDefaults:
    @patch("openbad.interoception.ebpf_probes.platform")
    @patch("openbad.interoception.ebpf_probes._import_bcc")
    def test_registers_and_loads_all_defaults(self, mock_import_bcc, mock_platform):
        mock_platform.system.return_value = "Linux"
        mock_bpf_cls = MagicMock()
        mock_import_bcc.return_value = mock_bpf_cls

        pm = ProbeManager()
        pm.load_defaults()

        assert len(pm.loaded_probes) == 2
        assert all(s == ProbeState.ATTACHED for s in pm.loaded_probes.values())


# ── ProbeManager.unload_all ────────────────────────────────────────


class TestUnloadAll:
    @patch("openbad.interoception.ebpf_probes.platform")
    @patch("openbad.interoception.ebpf_probes._import_bcc")
    def test_unloads_all_attached(self, mock_import_bcc, mock_platform):
        mock_platform.system.return_value = "Linux"
        mock_bpf = MagicMock()
        mock_bpf_cls = MagicMock(return_value=mock_bpf)
        mock_import_bcc.return_value = mock_bpf_cls

        pm = ProbeManager()
        pm.load_defaults()
        pm.unload_all()

        assert all(s == ProbeState.UNLOADED for s in pm.loaded_probes.values())
        assert mock_bpf.cleanup.call_count == 2


# ── _import_bcc ────────────────────────────────────────────────────


class TestImportBcc:
    def test_raises_when_bcc_not_installed(self):
        from openbad.interoception.ebpf_probes import _import_bcc

        with (
            patch.dict("sys.modules", {"bcc": None}),
            pytest.raises(ProbeError, match="'bcc' package is required"),
        ):
            _import_bcc()
