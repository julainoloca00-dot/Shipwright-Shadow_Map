from __future__ import annotations

from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "libultraship/src/fast/interpreter.cpp"
    text = path.read_text(encoding="utf-8")

    function_start = text.find("void Interpreter::RenderShadowVolumes() {")
    function_end = text.find("void Interpreter::GfxDpSetGrayscaleColor", function_start)
    if function_start < 0 or function_end < 0:
        raise RuntimeError("shadow merge build-order fix: RenderShadowVolumes bounds not found")

    function = text[function_start:function_end]
    world_start_marker = "    // Resolve static/world and physically large casters after compact actors."
    world_end_line = "    mEnvironmentShadowCasterCache.resize(stableEnvironmentFloats);"

    world_start = function.find(world_start_marker)
    world_end = function.find(world_end_line, world_start)
    if world_start < 0 or world_end < 0:
        raise RuntimeError("shadow merge build-order fix: moved world resolve block not found")
    world_end += len(world_end_line)

    world_block = function[world_start:world_end]
    function_without_world = function[:world_start] + function[world_end:]

    # The merge script previously used find(), which selected the early-return cleanup near the beginning of the
    # function. That placed the world resolve before effectiveCamera and the local-light arrays were initialized.
    # The last actor cleanup is the intended insertion point after all compact/Navi passes.
    final_clear_marker = "    mShadowCasterAccum.clear();"
    final_clear = function_without_world.rfind(final_clear_marker)
    if final_clear < 0:
        raise RuntimeError("shadow merge build-order fix: final actor cleanup not found")

    # Guard against accidentally selecting the early-return cleanup again.
    navi_resolve_marker = "    for (const NaviShadowCasterBatch& batch : mNaviShadowCasterBatches)"
    navi_resolve = function_without_world.find(navi_resolve_marker)
    if navi_resolve >= 0 and final_clear <= navi_resolve:
        raise RuntimeError("shadow merge build-order fix: selected cleanup is not after Navi actor resolves")

    function = (
        function_without_world[:final_clear]
        + world_block
        + "\n\n"
        + function_without_world[final_clear:]
    )

    text = text[:function_start] + function + text[function_end:]
    path.write_text(text, encoding="utf-8", newline="\n")
    print("Moved the merged world-shadow resolve to the final post-actor position for valid C++ scope/order.")


if __name__ == "__main__":
    main()
