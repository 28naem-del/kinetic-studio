"""Machine resources -> safe parallel worker count."""
from __future__ import annotations

import os


def mem_available_mb() -> float:
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    try:
        import psutil
        return psutil.virtual_memory().available / 1e6
    except Exception:  # noqa: BLE001
        return 2048.0


def cpu_count() -> int:
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:
        return os.cpu_count() or 2


def per_worker_mb(width: int, height: int, samples: int = 3) -> float:
    """Footprint of one render worker: Python+Skia+NumPy/OpenCV runtime (~250 MB), frame buffers
    and post temporaries (~64 B/px) and its x264 encoder (~70 B/px at medium). Calibrated on an
    8-CPU box: 7 workers at 1080x1920 peaked at 3.8 GB RSS total (~545 MB each)."""
    px = width * height
    return 250.0 + px * (64 + 70) / 1e6 + (8 * px / 1e6 if samples > 1 else 0)


def auto_workers(width: int, height: int, samples: int = 3, reserve_mb: float = 900.0,
                 max_workers: int | None = None, jobs: int | None = None) -> tuple[int, dict]:
    """Pick a worker count that fits in free RAM, leaving `reserve_mb` for the OS and other
    processes, never exceeding cpu-1 (x264 also uses threads) or the number of chunks."""
    free = mem_available_mb()
    per = per_worker_mb(width, height, samples)
    by_mem = int(max(0.0, free - reserve_mb) // per)
    by_cpu = max(1, cpu_count() - 1)
    n = max(1, min(by_mem, by_cpu))
    if max_workers:
        n = min(n, max_workers)
    if jobs:
        n = min(n, jobs)
    return n, {"mem_available_mb": round(free), "per_worker_mb": round(per), "by_mem": by_mem, "by_cpu": by_cpu,
               "workers": n}
