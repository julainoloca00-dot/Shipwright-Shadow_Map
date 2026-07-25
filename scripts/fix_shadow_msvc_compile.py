from pathlib import Path


root = Path(__file__).resolve().parents[1]
path = root / "libultraship/src/fast/interpreter.cpp"
text = path.read_text(encoding="utf-8")

old_actor_boundary = '''    // The first actor marker closes room-only environment capture. The actual resolve may occur after all
    // opaque actors so Link can receive shadows, but actor geometry must never leak into the static-world cache.
    mCaptureEnvironmentShadow = false;

    // Sentinel (gSPToonShadowFlush):'''
new_actor_boundary = '''    // The first actor marker closes room-only environment capture. The actual resolve may occur after all
    // opaque actors so Link can receive shadows, but actor geometry must never leak into the static-world cache.
    gfx->mCaptureEnvironmentShadow = false;

    // Sentinel (gSPToonShadowFlush):'''

count = text.count(old_actor_boundary)
if count != 1:
    raise RuntimeError(f"actor-boundary Interpreter access: expected exactly one match, found {count}")
text = text.replace(old_actor_boundary, new_actor_boundary, 1)

# Use the standard C++ overload instead of relying on the global C spelling exposed by a specific CRT.
text = text.replace("llroundf(", "std::llround(")

path.write_text(text, encoding="utf-8", newline="\n")
print("Applied MSVC-safe dynamic-shadow compile fixes.")
