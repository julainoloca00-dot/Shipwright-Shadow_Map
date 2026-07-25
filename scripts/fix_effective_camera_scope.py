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


def remove_existing_camera_block(function: str, declaration: int) -> str:
    """Remove one late effectiveCamera declaration and its complete initialization block."""
    declaration_line_start = function.rfind("\n", 0, declaration) + 1
    block_start = declaration_line_start

    # Include immediately adjacent descriptive comment lines, without depending on their wording.
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
        raise RuntimeError("effective camera scope: existing declaration has no matrix copy")

    widescreen_if = function.find("if (!mFbActive", memcpy_position)
    if widescreen_if < 0:
        # Conservative fallback: remove through the memcpy line only.
        block_end = function.find("\n", memcpy_position)
        if block_end < 0:
            block_end = memcpy_position + len(memcpy_marker)
        else:
            block_end += 1
    else:
        opening_brace = function.find("{", widescreen_if)
        closing_brace = find_matching_brace(function, opening_brace, "effective camera scope")
        block_end = closing_brace + 1
        while block_end < len(function) and function[block_end] in " \t\r":
            block_end += 1
        if block_end < len(function) and function[block_end] == "\n":
            block_end += 1

    return function[:block_start] + function[block_end:]


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
    memcpy_marker = "memcpy(effectiveCamera, &mRsp->P_matrix[0][0], sizeof(effectiveCamera));"
    first_render_call = function.find("RenderDynamicShadowMap(")
    if first_render_call < 0:
        raise RuntimeError("effective camera scope: no dynamic-shadow render call found")

    declaration_positions: list[int] = []
    search_from = 0
    while True:
        position = function.find(declaration_marker, search_from)
        if position < 0:
            break
        declaration_positions.append(position)
        search_from = position + len(declaration_marker)

    if len(declaration_positions) > 1:
        raise RuntimeError("effective camera scope: duplicate effectiveCamera declarations found")

    if declaration_positions:
        declaration = declaration_positions[0]
        memcpy_position = function.find(memcpy_marker, declaration)
        already_valid = (
            declaration < first_render_call
            and memcpy_position >= 0
            and memcpy_position < first_render_call
        )
        if already_valid:
            print("effectiveCamera already exists in outer scope before every shadow resolve.")
            return
        function = remove_existing_camera_block(function, declaration)

    camera_block = '''    // Shared by the world, ordinary actor and per-caster Navi shadow resolves. Earlier cache patches can
    // replace the original declaration, so recreate it here in the outer function scope when necessary.
    float effectiveCamera[16];
    memcpy(effectiveCamera, &mRsp->P_matrix[0][0], sizeof(effectiveCamera));
    if (!mFbActive && mCurDimensions.width > 0 && mCurDimensions.height > 0) {
        const float targetAspect = static_cast<float>(mCurDimensions.width) / static_cast<float>(mCurDimensions.height);
        const float aspectScale = (4.0f / 3.0f) / targetAspect;
        for (int row = 0; row < 4; row++) {
            effectiveCamera[row * 4] *= aspectScale;
        }
    }

'''

    initialization_anchor = "    mCaptureEnvironmentShadow = false;"
    anchor = function.find(initialization_anchor)
    if anchor >= 0:
        insertion = function.find("\n", anchor)
        if insertion < 0:
            raise RuntimeError("effective camera scope: initialization anchor line end not found")
        insertion += 1
    else:
        opening = function.find("{")
        insertion = function.find("\n", opening)
        if opening < 0 or insertion < 0:
            raise RuntimeError("effective camera scope: function-body insertion point not found")
        insertion += 1

    function = function[:insertion] + "\n" + camera_block + function[insertion:]

    final_declaration = function.find(declaration_marker)
    final_memcpy = function.find(memcpy_marker, final_declaration)
    final_first_render = function.find("RenderDynamicShadowMap(")
    duplicate = function.find(declaration_marker, final_declaration + len(declaration_marker))
    if final_declaration < 0 or duplicate >= 0:
        raise RuntimeError("effective camera scope: final declaration count is invalid")
    if final_memcpy < 0 or final_memcpy >= final_first_render:
        raise RuntimeError("effective camera scope: matrix is not initialized before the first shadow resolve")
    if final_declaration >= final_first_render:
        raise RuntimeError("effective camera scope: a shadow resolve still precedes the camera declaration")

    text = text[:function_start] + function + text[function_end:]
    path.write_text(text, encoding="utf-8", newline="\n")
    print("Created effectiveCamera in outer scope before every dynamic-shadow resolve.")


if __name__ == "__main__":
    main()
