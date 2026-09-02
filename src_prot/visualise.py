"""
CH2 Multi-Instrument Visualiser (Separate 1:1 Aspect Ratio Outputs)
===================================================================
Extracts and renders each Chandrayaan-2 instrument product into its own
separate high-resolution PNG with strict 1:1 true physical aspect ratio.

Outputs:
  - issdc_ch2_output/visualise/tmc_2.png
  - issdc_ch2_output/visualise/ohrc.png
  - issdc_ch2_output/visualise/iirs.png

Usage:
    python src/visualise.py
"""

import zipfile
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

DATA_DIR = Path("issdc_ch2_output/data")
OUT_DIR = Path("issdc_ch2_output/visualise")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def normalise_stretch(arr: np.ndarray) -> np.ndarray:
    """Robust 2nd-98th percentile linear contrast stretch to [0, 1]."""
    p2, p98 = np.percentile(arr, (2, 98))
    if p98 <= p2:
        return np.zeros_like(arr, dtype=np.float32)
    return np.clip((arr - p2) / (p98 - p2 + 1e-5), 0.0, 1.0)


def render_tmc(tmc_zip: Path) -> Path:
    print(f"[TMC-2] Processing {tmc_zip.name}...")
    with zipfile.ZipFile(tmc_zip) as zf:
        img_name = next(n for n in zf.namelist() if n.lower().endswith(".img") and "browse" not in n.lower())
        # 4000 samples wide; read a 4000x4000 square region
        start_line = 30000
        skip_bytes = start_line * 4000 * 2  # uint16 (2 bytes/sample)
        with zf.open(img_name) as fh:
            fh.seek(skip_bytes)
            raw = fh.read(4000 * 4000 * 2)
        arr = np.frombuffer(raw, dtype="<u2").reshape(4000, 4000).astype(np.float32)
        ds = arr[::4, ::4]  # downsample to 1000x1000
        norm = normalise_stretch(ds)

        fig, ax = plt.subplots(figsize=(8, 8), facecolor="#0b0b0b")
        ax.imshow(norm, cmap="gray", aspect="equal")
        ax.set_title("TMC-2 (Terrain Mapping Camera-2)\n1:1 Aspect Ratio (4,000 x 4,000 px Science Raster Detail)",
                     color="white", fontsize=13, fontweight="bold", pad=12)
        ax.set_xlabel(f"{tmc_zip.name} | Calibrated Fore (5m/px)", color="#999999", fontsize=9, labelpad=8)
        ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
        for spine in ax.spines.values():
            spine.set_edgecolor("#333333")
        plt.tight_layout()
        out_file = OUT_DIR / "tmc_2.png"
        plt.savefig(out_file, dpi=150, facecolor="#0b0b0b")
        plt.close()
        print(f"  -> Saved {out_file}")
        return out_file


def render_ohrc(ohr_zip: Path) -> Path:
    print(f"[OHRC] Processing {ohr_zip.name}...")
    with zipfile.ZipFile(ohr_zip) as zf:
        img_name = next(n for n in zf.namelist() if n.lower().endswith(".img") and "browse" not in n.lower())
        # 12000 samples wide; read lines 38000..42000 and center crop 4000x4000
        start_line = 38000
        skip_bytes = start_line * 12000  # uint8 (1 byte/sample)
        with zf.open(img_name) as fh:
            fh.seek(skip_bytes)
            raw = fh.read(4000 * 12000)
        arr = np.frombuffer(raw, dtype=np.uint8).reshape(4000, 12000).astype(np.float32)
        crop = arr[:, 4000:8000]  # 4000x4000 center square
        ds = crop[::4, ::4]        # downsample to 1000x1000
        norm = normalise_stretch(ds)

        fig, ax = plt.subplots(figsize=(8, 8), facecolor="#0b0b0b")
        ax.imshow(norm, cmap="gray", aspect="equal")
        ax.set_title("OHRC (Orbiter High Resolution Camera)\n1:1 Aspect Ratio (4,000 x 4,000 px Science Raster Detail)",
                     color="white", fontsize=13, fontweight="bold", pad=12)
        ax.set_xlabel(f"{ohr_zip.name} | Calibrated Primary (~0.25m/px)", color="#999999", fontsize=9, labelpad=8)
        ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
        for spine in ax.spines.values():
            spine.set_edgecolor("#333333")
        plt.tight_layout()
        out_file = OUT_DIR / "ohrc.png"
        plt.savefig(out_file, dpi=150, facecolor="#0b0b0b")
        plt.close()
        print(f"  -> Saved {out_file}")
        return out_file


def render_iirs(iir_zip: Path) -> Path:
    print(f"[IIRS] Processing {iir_zip.name}...")
    with zipfile.ZipFile(iir_zip) as zf:
        qub_name = next(n for n in zf.namelist() if n.lower().endswith(".qub"))
        # BSQ: band 64 of 256, 250 samples wide; read a 250x250 square region
        band = 64
        band_offset = band * 10736 * 250 * 2
        start_line = 7000
        skip_bytes = band_offset + (start_line * 250 * 2)
        with zf.open(qub_name) as fh:
            fh.seek(skip_bytes)
            raw = fh.read(250 * 250 * 2)
        arr = np.frombuffer(raw, dtype="<i2").reshape(250, 250).astype(np.float32)
        norm = normalise_stretch(arr)

        fig, ax = plt.subplots(figsize=(8, 8), facecolor="#0b0b0b")
        ax.imshow(norm, cmap="inferno", aspect="equal")
        ax.set_title("IIRS (Imaging Infrared Spectrometer)\n1:1 Aspect Ratio (250 x 250 px Spectral Band 64)",
                     color="white", fontsize=13, fontweight="bold", pad=12)
        ax.set_xlabel(f"{iir_zip.name} | Band 64 of 256 (~80m/px)", color="#999999", fontsize=9, labelpad=8)
        ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
        for spine in ax.spines.values():
            spine.set_edgecolor("#333333")
        plt.tight_layout()
        out_file = OUT_DIR / "iirs.png"
        plt.savefig(out_file, dpi=150, facecolor="#0b0b0b")
        plt.close()
        print(f"  -> Saved {out_file}")
        return out_file


def main():
    tmc_zip = next(DATA_DIR.glob("*_tmc_*.zip"), None)
    ohr_zip = next(DATA_DIR.glob("*_ohr_*.zip"), None)
    iir_zip = next(DATA_DIR.glob("*_iir_*.zip"), None)

    print("Generating separate 1:1 true aspect ratio images:")
    if tmc_zip: render_tmc(tmc_zip)
    if ohr_zip: render_ohrc(ohr_zip)
    if iir_zip: render_iirs(iir_zip)
    print("\nAll separate PNGs generated successfully in issdc_ch2_output/visualise/")


if __name__ == "__main__":
    main()
