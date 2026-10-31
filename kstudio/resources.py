"""Machine resources -> safe parallel worker count.

Memory probe order: Linux /proc/meminfo MemAvailable; macOS sysctl hw.memsize + vm_stat
(free + inactive + speculative + purgeable pages, i.e. what the kernel can hand out without
swapping, scaled down when kern.memorystatus_level reports pressure); psutil; a 2 GB fallback.

Worker cap. Besides free RAM and CPUs, the worker count is bounded by a total-RSS budget,
RSS_BUDGET_MB (default 3000 MB, env KSTUDIO_RSS_BUDGET_MB; 0 disables): parent process
(PARENT_MB) + n x per_worker_mb() must fit. At 1080x1920 that is (3000 - 150) // 900 = 3 workers
(estimate 2850 MB; measured peak of the whole process tree 2.7-2.8 GB with workers and encoders),
so a 1080p render stays under ~3 GB total RSS even when the laptop is under memory pressure
and another render holds the second render-lock slot; free-RAM headroom can only lower it further.
720x1280 gets 5 workers (~2.85 GB).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

RSS_BUDGET_MB = 3000.0   # total RSS (parent + workers + their encoders) a render may use
PARENT_MB = 150.0        # parent process while workers run (measured 40-50 MB; audio/mux run after the workers exit)


def _sysctl(name: str) -> int | None:
    try:
        r = subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, timeout=3)
        return int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def macos_memory() -> dict[str, float] | None:
    """Total / available MB on macOS without extra dependencies (sysctl + vm_stat)."""
    total = _sysctl("hw.memsize")
    if not total:
        return None
    try:
        out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"page size of (\d+) bytes", out)
    page = int(m.group(1)) if m else 4096
    pages: dict[str, int] = {}
    for line in out.splitlines():
        k, _, v = line.partition(":")
        v = v.strip().rstrip(".")
        if v.isdigit():
            pages[k.strip().lower()] = int(v)
    avail_pages = sum(pages.get(k, 0) for k in ("pages free", "pages inactive", "pages speculative",
                                                "pages purgeable"))
    avail = avail_pages * page / 2 ** 20
    level = _sysctl("kern.memorystatus_level")  # % of memory the kernel considers free; low = pressure
    if level is not None and 0 < level < 100:
        avail = min(avail, max(avail * 0.5, total / 2 ** 20 * level / 100.0))
    return {"total_mb": total / 2 ** 20, "available_mb": avail, "pressure_level": float(level or 0),
            "page_size": float(page)}


def rss_budget_mb() -> float:
    v = os.environ.get("KSTUDIO_RSS_BUDGET_MB")
    try:
        return float(v) if v not in (None, "") else RSS_BUDGET_MB
    except ValueError:
        return RSS_BUDGET_MB


def mem_available_mb() -> float:
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    mac = macos_memory() if sys.platform == "darwin" else None
    if mac:
        return mac["available_mb"]
    try:
        import psutil
        return psutil.virtual_memory().available / 1e6
    except Exception:  # noqa: BLE001
        return 2048.0


def cpu_count() -> int:
    affinity = getattr(os, "sched_getaffinity", None)
    if affinity is not None:
        try:
            return len(affinity(0))
        except AttributeError:
            pass
    return os.cpu_count() or 2


def per_worker_mb(width: int, height: int, samples: int = 3) -> float:
    """Peak footprint of one render worker plus its x264 encoder: Python+Skia+NumPy/OpenCV runtime
    (~250 MB) + frame buffers and post temporaries (~120 B/px) + the libx264 process (~185 B/px with
    lookahead at the standard preset). Calibrated on an M-series 24 GB laptop rendering the kinetic
    example at 1080x1920: worker processes peaked at 365-498 MB and their encoders at 234-384 MB,
    i.e. up to ~880 MB per worker (this formula: ~900 MB at 1080x1920, ~540 MB at 720x1280)."""
    px = width * height
    return 250.0 + px * (120 + 185) / 1e6 + (8 * px / 1e6 if samples > 1 else 0)


def auto_workers(width: int, height: int, samples: int = 3, reserve_mb: float = 900.0,
                 max_workers: int | None = None, jobs: int | None = None) -> tuple[int, dict]:
    """Pick a worker count that fits in free RAM, leaving `reserve_mb` for the OS and other
    processes, never exceeding cpu-1 (x264 also uses threads), the total-RSS budget
    (rss_budget_mb(), see module doc) or the number of chunks."""
    free = mem_available_mb()
    per = per_worker_mb(width, height, samples)
    by_mem = int(max(0.0, free - reserve_mb) // per)
    by_cpu = max(1, cpu_count() - 1)
    budget = rss_budget_mb()
    by_budget = int(max(0.0, budget - PARENT_MB) // per) if budget > 0 else by_cpu
    n = max(1, min(by_mem, by_cpu, by_budget))
    if max_workers:
        n = min(n, max_workers)
    if jobs:
        n = min(n, jobs)
    return n, {"mem_available_mb": round(free), "per_worker_mb": round(per), "by_mem": by_mem, "by_cpu": by_cpu,
               "rss_budget_mb": round(budget), "by_budget": by_budget,
               "est_total_rss_mb": round(PARENT_MB + n * per), "workers": n}
