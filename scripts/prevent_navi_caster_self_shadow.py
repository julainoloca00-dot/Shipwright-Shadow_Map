from __future__ import annotations

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    root = Path(__file__).resolve().parents[1]

    # Store a receiver-height cutoff for every individual Navi-directed caster. The caster's own body sits above
    # this plane and must not receive the shadow generated from its own geometry. World/environment shadow passes
    # remain unchanged, so Link can still become dark when he genuinely stands beneath a tree, roof or other caster.
    header_path = root / "libultraship/include/fast/interpreter.h"
    header_text = header_path.read_text(encoding="utf-8")
    if "float receiverMaxY = 0.0f;" not in header_text:
        header_text = replace_once(
            header_text,
            """        float center[3] = { 0.0f, 0.0f, 0.0f };\n    };""",
            """        float center[3] = { 0.0f, 0.0f, 0.0f };\n        float receiverMaxY = 0.0f;\n    };""",
            "Navi caster receiver cutoff member",
        )
    header_path.write_text(header_text, encoding="utf-8", newline="\n")

    interpreter_path = root / "libultraship/src/fast/interpreter.cpp"
    interpreter_text = interpreter_path.read_text(encoding="utf-8")

    if "batch.receiverMaxY" not in interpreter_text:
        interpreter_text = replace_once(
            interpreter_text,
            """            batch.center[0] = center[0];\n            batch.center[1] = center[1];\n            batch.center[2] = center[2];\n            mNaviShadowCasterBatches.push_back(batch);""",
            """            batch.center[0] = center[0];\n            batch.center[1] = center[1];\n            batch.center[2] = center[2];\n            // Allow the floor and small terrain height differences to receive the shadow, while excluding the\n            // caster's legs, torso and head from its own resolve.\n            batch.receiverMaxY = minimum[1] + 18.0f;\n            mNaviShadowCasterBatches.push_back(batch);""",
            "Navi caster receiver cutoff assignment",
        )

    if "selfShadowReceiverMask" not in interpreter_text:
        interpreter_text = replace_once(
            interpreter_text,
            """        const size_t actorVertexCount = batch.floatCount / 3;\n        mRapi->RenderDynamicShadowMap(\n            mNaviShadowCasterAccum.data() + batch.firstFloat, actorVertexCount, effectiveCamera, naviLightDirection,\n            batch.center, noLocalFill, kDynamicShadowMapResolution,""",
            """        // A large negative sentinel selects receiver filtering in the DX11 resolve shader. X carries\n        // the maximum receiver height; pixels above it belong to the caster itself and are discarded.\n        const float selfShadowReceiverMask[4] = { batch.receiverMaxY, 0.0f, 0.0f, -8192.0f };\n        const size_t actorVertexCount = batch.floatCount / 3;\n        mRapi->RenderDynamicShadowMap(\n            mNaviShadowCasterAccum.data() + batch.firstFloat, actorVertexCount, effectiveCamera, naviLightDirection,\n            batch.center, selfShadowReceiverMask, kDynamicShadowMapResolution,""",
            "Navi caster self-shadow receiver mask",
        )

    interpreter_path.write_text(interpreter_text, encoding="utf-8", newline="\n")

    # Patch both the normal and MSAA resolve shaders. Negative radii in the ordinary range still select the weak
    # world fill. Only the reserved -8192 sentinel activates caster-body receiver rejection.
    backend_path = root / "libultraship/src/fast/backends/gfx_direct3d11_shadow_map.inc"
    backend_text = backend_path.read_text(encoding="utf-8")
    old_shader_block = """    float localFill = 0.0;\n    const float localRadius = abs(localLight.w);\n    if (localRadius > 0.0) {\n        localFill = saturate(1.0 - length(world.xyz - localLight.xyz) / localRadius);\n        localFill = localFill * localFill * (3.0 - 2.0 * localFill);\n    }\n    const float localFillStrength = localLight.w < 0.0 ? 0.35 : 0.85;\n    const float localShadowScale = 1.0 - localFill * localFillStrength;"""
    new_shader_block = """    const bool casterReceiverOnly = localLight.w <= -4096.0;\n    if (casterReceiverOnly && world.y > localLight.x) discard;\n\n    float localFill = 0.0;\n    const float localRadius = casterReceiverOnly ? 0.0 : abs(localLight.w);\n    if (localRadius > 0.0) {\n        localFill = saturate(1.0 - length(world.xyz - localLight.xyz) / localRadius);\n        localFill = localFill * localFill * (3.0 - 2.0 * localFill);\n    }\n    const float localFillStrength = (!casterReceiverOnly && localLight.w < 0.0) ? 0.35 : 0.85;\n    const float localShadowScale = 1.0 - localFill * localFillStrength;"""

    shader_matches = backend_text.count(old_shader_block)
    if shader_matches != 2:
        raise RuntimeError(f"Navi self-shadow shader filter: expected two matches, found {shader_matches}")
    backend_text = backend_text.replace(old_shader_block, new_shader_block)
    backend_path.write_text(backend_text, encoding="utf-8", newline="\n")

    print("Prevented Navi-directed casters from shadowing their own bodies while preserving world shadows on Link.")


if __name__ == "__main__":
    main()
