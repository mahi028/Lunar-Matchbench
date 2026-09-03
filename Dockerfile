# Lunar-MatchBench container build.
#
# Targets Hugging Face Spaces (Docker SDK) first, and works unchanged on
# Railway, Render or any host that injects $PORT.
#
# libgl1/libglib2.0-0 are required for opencv-python to import at all on a
# minimal Debian base -- without them the process crashes on startup with
# "libGL.so.1: cannot open shared object file", a very common deploy gotcha
# that has nothing to do with your actual code.
FROM python:3.14-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install the CPU-only PyTorch build first. The default PyPI `torch` wheel
# bundles the full CUDA/cuDNN/cuBLAS stack (several GB) for GPU support --
# useless on CPU-only hosting, and it turns this image into a ~6.7GB monster
# that's slow to build, push, and pull for no benefit. The CPU wheel satisfies
# pyproject.toml's `torch>=2.13.0` constraint, so `pip install .` below reuses
# it instead of pulling the GPU build over it.
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# The baked preset runs. Without these a credential-less deployment can show
# nothing at all, so they are part of the image, not an optional extra.
COPY demo ./demo

# Hugging Face Spaces runs the container as uid 1000, so anything the app
# writes at runtime has to be owned by that user before the drop. Job records
# and generated imagery land in outputs/.
RUN useradd -m -u 1000 app \
    && mkdir -p /app/outputs/jobs /app/outputs/posters/raw /app/outputs/overlap /app/data_store/cache \
    && chown -R app:app /app
USER app

ENV PYTHONUNBUFFERED=1 \
    # The package is pip-installed, so walking up from its __file__ lands in
    # site-packages, not here. The demo bundle and everything writable hang off
    # this, so it has to be stated rather than inferred.
    LMB_PROJECT_ROOT=/app \
    MPLCONFIGDIR=/tmp/mpl \
    HF_HOME=/tmp/hf \
    TORCH_HOME=/tmp/torch \
    PORT=7860 \
    # Safe by default: a public image must not run every visitor's registration
    # on an operator's ISSDC account just because a credential leaked into the
    # environment. Set LMB_DEMO_ONLY=0 for a private deployment that should
    # fetch live.
    LMB_DEMO_ONLY=1

EXPOSE 7860

# $PORT is honoured so the same image works on Spaces (7860), Railway and
# Render (injected) without a per-host start command.
CMD ["sh", "-c", "python -m lunar_matchbench.cli serve --host 0.0.0.0 --port ${PORT:-7860}"]
