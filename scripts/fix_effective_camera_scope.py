from __future__ import annotations

from pathlib import Path


def find_matching_brace(text: str, opening_brace: int, label: str) -> int:
    if opening_brace < 0 or opening_brace >= len(text) or text[opening_brace] != "{":
        raise RuntimeError(f"{label}: invalid opening brace")

    depth = 0
    for index in range(opening_brace, len(text)):
        character = text[index]
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return index

    raise RuntimeError(f"{label}: matching closing brace not found")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "libultraship/src/fast/interpreter.cpp"
    text = path.read_text(encoding="utf-8")

    function_start = text.find("void Interpreter::RenderShadowVolumes() {")
    function_end = text.find("void Interpreter::GfxDpSetGrayscaleColor", function_start)
    if function_start < 0 or function_end < 0:
        raise RuntimeError("effective camera scope: RenderShadowVolumes bounds not found")

    function = text[function_start:function_end]
    declaration_marker = "    float effectiveCamera[16];"
    declaration = function.find(declaration_marker)
    if declaration < 0:
        raise RuntimeError("effective camera scope: effectiveCamera declaration not found")
    if function.find(declaration_marker, declaration + len(declaration_marker)) >= 0:
        raise RuntimeError("effective camera scope: duplicate effectiveCamera declaration found")

    first_render_call = function.find("RenderDynamicShadowMap(")
    if first_render_call < 0:
        raise RuntimeError("effective camera scope: no dynamic-shadow render call found")

    # The declaration is already in the outer scope and before every resolve. Keep the script idempotent.
    if declaration < first_render_call:
        print("effectiveCamera is already initialized before every dynamic-shadow resolve.")
        return

    # Include the two descriptive comment lines when they are immediately above the declaration, but do not rely
    # on their exact wording. This makes the move resilient to earlier scripts changing comments or whitespace.
    declaration_line_start = function.rfind("\n", 0, declaration) + 1
    block_start = declaration_line_start
    scan_line_start = declaration_line_start
    for _ in range(3):
        previous_line_end = scan_line_start - 1
        if previous_line_end <= 0:
            break
        previous_line_start = function.rfind("\n", 0, previous_line_end) + 1
        previous_line = function[previous_line_start:previous_line_end].lstrip()
        if not previous_line.startswith("//"):
            break
        block_start = previous_line_start
        scan_line_start = previous_line_start

    memcpy_marker = "memcpy(effectiveCamera, &mRsp->P_matrix[0][0], sizeof(effectiveCamera));"
    memcpy_position = function.find(memcpy_marker, declaration)
    if memcpy_position < 0:
        raise RuntimeError("effective camera scope: matrix initialization copy not found")

    widescreen_if = function.find("if (!mFbActive", memcpy_position)
    if widescreen_if < 0:
        raise RuntimeError("effective camera scope: widescreen adjustment block not found")
    opening_brace = function.find("{", widescreen_if)
    closing_brace = find_matching_brace(function, opening_brace, "effective camera scope")

    block_end = closing_brace + 1
    while block_end < len(function) and function[block_end] in " \t":
        block_end += 1
    if block_end < len(function) and function[block_end] == "\r":
        block_end += 1
    if block_end < len(function) and function[block_end] == "\n":
        block_end += 1

    camera_block = function[block_start:block_end]
    if declaration_marker not in camera_block or memcpy_marker not in camera_block:
        raise RuntimeError("effective camera scope: extracted matrix block is incomplete")

    function_without_camera = function[:block_start] + function[block_end:]

    initialization_anchor = "    mCaptureEnvironmentShadow = false;"
    anchor = function_without_camera.find(initialization_anchor)
    if anchor >= 0:
        insertion = function_without_camera.find("\n", anchor)
        if insertion < 0:
            raise RuntimeError("effective camera scope: initialization anchor line end not found")
        insertion += 1
    else:
        # Fallback to immediately after the function opening brace. This is still the outer function scope.
        opening = function_without_camera.find("{")
        insertion = function_without_camera.find("\n", opening)
        if opening < 0 or insertion < 0:
            raise RuntimeError("effective camera scope: function-body insertion point not found")
        insertion += 1

    function = (
        function_without_camera[:insertion]
        + "\n"
        + camera_block
        + function_without_camera[insertion:]
    )

    final_declaration = function.find(declaration_marker)
    final_first_render = function.find("RenderDynamicShadowMap(")
    if final_declaration < 0 or function.find(declaration_marker, final_declaration + len(declaration_marker)) >= 0:
        raise RuntimeError("effective camera scope: final declaration count is invalid")
    if final_first_render < 0 or final_declaration >= final_first_render:
        raise RuntimeError("effective camera scope: a shadow resolve still precedes the camera declaration")

    text = text[:function_start] + function + text[function_end:]
    path.write_text(text, encoding="utf-8", newline="\n")
    print("Moved effectiveCamera structurally to outer scope before every shadow resolve.")


if __name__ == "__main__":
    main()
