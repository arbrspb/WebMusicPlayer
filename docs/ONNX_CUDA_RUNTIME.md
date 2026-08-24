# CUDA runtime for Discogs Multi-EffNet

Discogs Multi-EffNet uses ONNX Runtime independently from Whisper:

- Multi-EffNet: `onnxruntime-gpu==1.17.0`, CUDA 12 build, NumPy `1.24.3`;
- Whisper: Faster Whisper / CTranslate2 and its own CUDA runtime;
- the two runtimes must not share or globally replace DLLs.

Install the common environment first:

```powershell
python -m pip install -r requirements.txt
```

For a portable CPU-only installation:

```powershell
python -m pip install -r requirements-onnx-cpu.txt
```

For the verified CUDA 12 ONNX Runtime variant, remove only the conflicting CPU
package and install the dedicated file:

```powershell
python -m pip uninstall -y onnxruntime
python -m pip install -r requirements-cuda.txt
```

Do not upgrade NumPy to 2.x in this environment and do not install
`onnxruntime` and `onnxruntime-gpu` together.

ONNX Runtime 1.17 may additionally require compatible cuDNN 8 and
`zlibwapi.dll`. Put those local files in:

```text
runtime\onnx_cuda\bin
```

`app/deep_embeddings.py` registers this directory with
`os.add_dll_directory()` before ONNX Runtime provider/session initialization
and retains the returned handle for the process lifetime. The directory is
ignored by Git; NVIDIA, cuDNN and zlib binaries are not distributed with the
project. The directory is optional: when absent, provider diagnostics and the
existing CPU fallback remain available.

Never add this directory globally to Windows `PATH`: that can break the
independent CTranslate2 CUDA configuration used by Faster Whisper.
