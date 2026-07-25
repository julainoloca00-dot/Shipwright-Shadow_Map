from __future__ import annotations

from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "libultraship/src/fast/interpreter.cpp"
    text = path.read_text(encoding="utf-8")

    function_start = text.find("void Interpreter::RenderShadowVolumes() {")
    function_end = text.find("void Interpreter::GfxDpSetGrayscaleColor", function_start)
    if function_start < 0 or function_end < 0:
        raise RuntimeError("effective camera scope: RenderShadowVolumes bounds not found")

    function = text[function_start:function_end]

    camera_comment = (
        "    // Fast3D applies its widescreen correction to clip X after P_matrix. Pass the exact effective matrix so\n"
        "    // world reconstruction from the DX11 depth buffer remains stable while the camera rotates.\n"
    )
    camera_start = function.find(camera_comment)
    if camera_start < 0:
        raise RuntimeError("effective camera scope: camera initialization comment not found")

    camera_end_markers = (
        "    // Large dynamic casters join the world cache only for this frame's weak-fill resolve.",
        "    // Resolve static/world and physically large casters first.",
        "    // Compact actors outside Navi's influence retain the stable environmental direction.",
    )
    camera_end = -1
    for marker in camera_end_markers:
        candidate = function.find(marker, camera_start + len(camera_comment))
        if candidate >= 0 and (camera_end < 0 or candidate < camera_end):
            camera_end = candidate
    if camera_end < 0:
        raise RuntimeError("effective camera scope: end of camera initialization block not found")

    camera_block = function[camera_start:camera_end]
    if camera_block.count("float effectiveCamera[16];") != 1:
        raise RuntimeError("effective camera scope: expected one matrix declaration in initialization block")
    if "memcpy(effectiveCamera, &mRsp->P_matrix[0][0], sizeof(effectiveCamera));" not in camera_block:
        raise RuntimeError("effective camera scope: matrix copy was not found in initialization block")

    function_without_camera = function[:camera_start] + function[camera_end:]

    initialization_anchor = "    mCaptureEnvironmentShadow = false;\n"
    anchor = function_without_camera.find(initialization_anchor)
    if anchor < 0:
        raise RuntimeError("effective camera scope: function initialization anchor not found")
    insertion = anchor + len(initialization_anchor)

    # The camera matrix is shared by world, ordinary actor and per-caster Navi resolves. Keep it in the outer
    # function scope and initialize it before any branch can submit a shadow-map pass.
    function = (
        function_without_camera[:insertion]
        + "\n"
        + camera_block
        + function_without_camera[insertion:]
    )

    declaration = function.find("float effectiveCamera[16];")
    if declaration < 0 or function.count("float effectiveCamera[16];") != 1:
        raise RuntimeError("effective camera scope: final declaration count is invalid")

    render_calls = []
    search_from = 0
    while True:
        call = function.find("RenderDynamicShadowMap(", search_from)
        if call < 0:
            break
        render_calls.append(call)
        search_from = call + 1
    if not render_calls:
        raise RuntimeError("effective camera scope: no dynamic shadow render calls found")
    if any(call < declaration for call in render_calls):
        raise RuntimeError("effective camera scope: a shadow render call still precedes the matrix declaration")

    text = text[:function_start] + function + text[function_end:]
    path.write_text(text, encoding="utf-8", newline="\n")
    print("Moved the effective shadow camera matrix to outer function scope before every shadow resolve.")


if __name__ == "__main__":
    main()
