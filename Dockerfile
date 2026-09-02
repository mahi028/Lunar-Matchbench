# Lunar-MatchBench container build.
#
# libgl1/libglib2.0-0 are required for opencv-python to import at all on a
# minimal Debian base -- without them the process crashes on startup with
# "libGL.so.1: cannot open shared object file", a very common deploy gotcha
# that has nothing to do with your actual code.
FROM python:3.14-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

# Install the CPU-only PyTorch build first. The default PyPI `torch` wheel
# bundles the full CUDA/cuDNN/cuBLAS stack (several GB) for GPU support --
# useless on Railway's CPU-only hosting, and it turns this image into a
# ~6.7GB monster that's slow to build, push, and pull for no benefit. The
# CPU wheel satisfies pyproject.toml's `torch>=2.13.0` constraint, so `pip
# install .` below reuses it instead of pulling the GPU build over it.
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir .

# glibc's malloc gives each thread its own memory arena, and on a
# constrained container those arenas are never handed back to the OS even
# after the tensors/arrays inside them are freed -- RSS just climbs with
# every request that spins up native threads (torch/numpy/opencv all do).
# Capping arenas, plus capping how many threads torch/OpenBLAS/OpenMP get to
# spin up in the first place, is the standard fix for a Python+numpy/torch
# container that OOMs after a handful of requests despite a single request's
# own peak usage staying well under the memory limit. On a 1-2 vCPU host,
# these don't cost meaningful speed either -- there's little real
# parallelism to lose.
ENV MALLOC_ARENA_MAX=2 \
    OMP_NUM_THREADS=2 \
    MKL_NUM_THREADS=2 \
    OPENBLAS_NUM_THREADS=2

# Local-run fallback only. Railway overrides this with its own Start Command
# (see railway.json) so the app binds to Railway's injected $PORT instead of
# a fixed port.
EXPOSE 8000
CMD ["python", "-m", "lunar_matchbench.cli", "serve", "--host", "0.0.0.0", "--port", "8000"]
