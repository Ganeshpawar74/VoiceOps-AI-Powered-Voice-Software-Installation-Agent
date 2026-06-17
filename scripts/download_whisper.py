"""
Pre-download faster-whisper model to avoid cold-start delay.
Run: python scripts/download_whisper.py
"""
from faster_whisper import WhisperModel
import os

size = os.getenv("STT_WHISPER_MODEL_SIZE", "base")
device = os.getenv("STT_WHISPER_DEVICE", "cpu")
compute = os.getenv("STT_WHISPER_COMPUTE_TYPE", "int8")

print(f"Downloading faster-whisper model: {size} (device={device}, compute={compute})")
print("This downloads to ~/.cache/huggingface/hub (one-time only)...")
model = WhisperModel(size, device=device, compute_type=compute)
print(f"✅ Model '{size}' ready!")
print(f"   Approx sizes: tiny=75MB  base=145MB  small=466MB  medium=1.5GB  large-v3=3GB")
