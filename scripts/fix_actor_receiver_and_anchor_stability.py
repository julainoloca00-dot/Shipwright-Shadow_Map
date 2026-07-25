from __future__ import annotations

import re
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def replace_regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.DOTALL)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one structural match, found {count}")
    return updated


def patch_game_shadow_policy(root: Path) -> None:
    path = root / "soh/soh/Enhancements/Graphics/ZZNaviShadowLightFix.cpp"
    text = path.read_text(encoding="utf-8")

    if "Use the resolved floor as the world-shadow anchor height" not in text:
        text = replace_once(
            text,
            """            anchor[0] = player->actor.world.pos.x;\n            anchor[1] = player->actor.world.pos.y;\n            anchor[2] = player->actor.world.pos.z;""",
            """            anchor[0] = player->actor.world.pos.x;\n            // Use the resolved floor as the world-shadow anchor height. The player's animated/model centre must\n            // never move the receiver plane or make the shadow map swim while the camera changes.\n            anchor[1] = player->actor.floorPoly != nullptr ? player->actor.floorHeight : player->actor.world.pos.y;\n            anchor[2] = player->actor.world.pos.z;""",
            "player floor-height shadow anchor",
        )

    if "Every compact caster publishes its real floor plane" not in text:
        text = replace_once(
            text,
            """    const float clampedFloor = std::clamp(floorHeight, -32767.0f, 32767.0f);\n    const s16 feetClamp = actor->id == ACTOR_EN_KANBAN ? static_cast<s16>(clampedFloor)\n                                                       : static_cast<s16>(TOON_SHADOW_NO_CLAMP);""",
            """    const float clampedFloor = std::clamp(floorHeight, -32767.0f, 32767.0f);\n    // Every compact caster publishes its real floor plane. The dynamic shadow receiver mask consumes this value\n    // to keep the actor's own body and vertical walls out of its projected shadow.\n    const s16 feetClamp = static_cast<s16>(clampedFloor);""",
            "publish actor floor clamp",
        )

    path.write_text(text, encoding="utf-8", newline="\n")


def patch_interpreter_header(root: Path) -> None:
    path = root / "libultraship/include/fast/interpreter.h"
    text = path.read_text(encoding="utf-8")

    if "float shadowAnchor[3]" not in text:
        text = replace_regex_once(
            text,
            r"        float center\[3\] = \{ 0\.0f, 0\.0f, 0\.0f \};\s*\n"
            r"        float receiverMaxY = 0\.0f;",
            """        // Quantized actor centre used only for choosing a stable light direction.\n        float center[3] = { 0.0f, 0.0f, 0.0f };\n        // Quantized XZ plus the real collision-floor Y used to lock the compact shadow map to the world.\n        float shadowAnchor[3] = { 0.0f, 0.0f, 0.0f };\n        float receiverFloorY = 0.0f;\n        float receiverRadius = 64.0f;\n        bool useNaviDirection = false;""",
            "individual compact-caster receiver metadata",
        )

    path.write_text(text, encoding="utf-8", newline="\n")


def patch_interpreter(root: Path) -> None:
    path = root / "libultraship/src/fast/interpreter.cpp"
    text = path.read_text(encoding="utf-8")

    text = replace_regex_once(
        text,
        r"    constexpr size_t kMaxNaviShadowBatches = 6;\s*\n"
        r"    const bool useNaviBatch =\s*\n"
        r"        !largeCaster && insideNaviRadius && mNaviShadowCasterBatches\.size\(\) < kMaxNaviShadowBatches;\s*\n"
        r"    std::vector<float>& destination = largeCaster\s*\n"
        r"\s*\? mLargeShadowCasterAccum\s*\n"
        r"\s*: \(useNaviBatch \? mNaviShadowCasterAccum : mShadowCasterAccum\);",
        """    // The player always receives an individual pass, even without Navi. This is detected using the stable\n    // player/floor anchor published by the game, so Link never falls back into the combined actor pass that can\n    // project onto his own body. Navi-affected neighbours remain individually bounded, with a strict pass cap.\n    const float playerDx = center[0] - mDynamicShadowAnchor[0];\n    const float playerDy = center[1] - mDynamicShadowAnchor[1];\n    const float playerDz = center[2] - mDynamicShadowAnchor[2];\n    const bool isPlayerCaster = !largeCaster && playerDx * playerDx + playerDz * playerDz <= 48.0f * 48.0f &&\n                                std::fabs(playerDy) <= 192.0f;\n    constexpr size_t kMaxNaviAffectedShadowBatches = 7;\n    const bool useIndividualBatch =\n        !largeCaster &&\n        (isPlayerCaster ||\n         (insideNaviRadius && mNaviShadowCasterBatches.size() < kMaxNaviAffectedShadowBatches));\n    std::vector<float>& destination = largeCaster\n                                          ? mLargeShadowCasterAccum\n                                          : (useIndividualBatch ? mNaviShadowCasterAccum : mShadowCasterAccum);""",
        "route Link and Navi casters individually",
    )

    text = replace_regex_once(
        text,
        r"            if \(useNaviBatch\) \{.*?"
        r"                mNaviShadowCasterBatches\.push_back\(batch\);\s*\n"
        r"            \}",
        """            if (useIndividualBatch) {\n                NaviShadowCasterBatch batch;\n                batch.firstFloat = firstFloat;\n                batch.floatCount = copyFloats;\n\n                // Actor bounds move slightly with animation. Quantizing their representative centre and the\n                // projection anchor prevents those harmless mesh changes from rotating or sliding the shadow map.\n                constexpr float kCompactAnchorGrid = 4.0f;\n                auto snapCompactAnchor = [](float value) {\n                    return std::floor(value / kCompactAnchorGrid + 0.5f) * kCompactAnchorGrid;\n                };\n                batch.center[0] = snapCompactAnchor(center[0]);\n                batch.center[1] = snapCompactAnchor(center[1]);\n                batch.center[2] = snapCompactAnchor(center[2]);\n\n                const float receiverFloorY =\n                    mRsp->toon_shadow_clamp_feet ? mRsp->toon_shadow_feet_clamp_y : minimum[1];\n                batch.shadowAnchor[0] = batch.center[0];\n                batch.shadowAnchor[1] = receiverFloorY;\n                batch.shadowAnchor[2] = batch.center[2];\n                batch.receiverFloorY = receiverFloorY;\n                const float horizontalExtent = std::max(halfExtentX, halfExtentZ);\n                batch.receiverRadius = std::clamp(horizontalExtent * 1.75f + 32.0f, 56.0f, 144.0f);\n                batch.useNaviDirection = insideNaviRadius;\n                mNaviShadowCasterBatches.push_back(batch);\n            }""",
        "store stable per-caster floor and anchor",
    )

    text = replace_regex_once(
        text,
        r"        if \(batch\.floatCount < 9 \|\| batch\.firstFloat \+ batch\.floatCount > "
        r"mNaviShadowCasterAccum\.size\(\) \|\|\s*\n"
        r"            mDynamicShadowLocalLight\[3\] <= 0\.0f\) \{",
        """        if (batch.floatCount < 9 ||\n            batch.firstFloat + batch.floatCount > mNaviShadowCasterAccum.size()) {""",
        "allow environment-directed Link individual pass",
    )

    direction_start = text.find("        float naviLightDirection[3] = {")
    direction_end_marker = "        // A large negative sentinel selects receiver filtering in the DX11 resolve shader."
    direction_end = text.find(direction_end_marker, direction_start)
    if direction_start < 0 or direction_end < 0:
        raise RuntimeError("stable individual light direction: block bounds not found")

    direction_block = """        float casterLightDirection[3] = { mDynamicShadowLightDir[0], mDynamicShadowLightDir[1],\n                                                  mDynamicShadowLightDir[2] };\n        if (batch.useNaviDirection && mDynamicShadowLocalLight[3] > 0.0f) {\n            casterLightDirection[0] = mDynamicShadowLocalLight[0] - batch.center[0];\n            casterLightDirection[1] = mDynamicShadowLocalLight[1] - batch.center[1];\n            casterLightDirection[2] = mDynamicShadowLocalLight[2] - batch.center[2];\n            float directionLengthSquared = casterLightDirection[0] * casterLightDirection[0] +\n                                           casterLightDirection[1] * casterLightDirection[1] +\n                                           casterLightDirection[2] * casterLightDirection[2];\n            if (directionLengthSquared > 0.000001f) {\n                const float inverseLength = 1.0f / std::sqrt(directionLengthSquared);\n                casterLightDirection[0] *= inverseLength;\n                casterLightDirection[1] *= inverseLength;\n                casterLightDirection[2] *= inverseLength;\n\n                constexpr float kMinimumNaviElevation = 0.45f;\n                if (casterLightDirection[1] < kMinimumNaviElevation) {\n                    const float horizontalLength =\n                        std::sqrt(casterLightDirection[0] * casterLightDirection[0] +\n                                  casterLightDirection[2] * casterLightDirection[2]);\n                    const float horizontalTarget =\n                        std::sqrt(1.0f - kMinimumNaviElevation * kMinimumNaviElevation);\n                    if (horizontalLength > 0.00001f) {\n                        const float horizontalScale = horizontalTarget / horizontalLength;\n                        casterLightDirection[0] *= horizontalScale;\n                        casterLightDirection[2] *= horizontalScale;\n                    } else {\n                        casterLightDirection[0] = horizontalTarget;\n                        casterLightDirection[2] = 0.0f;\n                    }\n                    casterLightDirection[1] = kMinimumNaviElevation;\n                }\n            } else {\n                casterLightDirection[0] = mDynamicShadowLightDir[0];\n                casterLightDirection[1] = mDynamicShadowLightDir[1];\n                casterLightDirection[2] = mDynamicShadowLightDir[2];\n            }\n        }\n\n"""
    text = text[:direction_start] + direction_block + text[direction_end:]

    text = replace_regex_once(
        text,
        r"        // A large negative sentinel selects receiver filtering in the DX11 resolve shader\..*?"
        r"        const float selfShadowReceiverMask\[4\] = \{ batch\.receiverMaxY, 0\.0f, 0\.0f, -8192\.0f \};\s*\n"
        r"        const size_t actorVertexCount = batch\.floatCount / 3;\s*\n"
        r"        mRapi->RenderDynamicShadowMap\(\s*\n"
        r"            mNaviShadowCasterAccum\.data\(\) \+ batch\.firstFloat, actorVertexCount, effectiveCamera, "
        r"naviLightDirection,\s*\n"
        r"            batch\.center, selfShadowReceiverMask, kDynamicShadowMapResolution,",
        """        // The receiver mask carries floor Y, stable XZ centre and a bounded horizontal radius. The negative\n        // sentinel distinguishes it from ordinary signed Navi-fill radii without adding another API parameter.\n        const float selfShadowReceiverMask[4] = {\n            batch.receiverFloorY, batch.shadowAnchor[0], batch.shadowAnchor[2],\n            -(8192.0f + batch.receiverRadius),\n        };\n        const size_t actorVertexCount = batch.floatCount / 3;\n        mRapi->RenderDynamicShadowMap(\n            mNaviShadowCasterAccum.data() + batch.firstFloat, actorVertexCount, effectiveCamera, casterLightDirection,\n            batch.shadowAnchor, selfShadowReceiverMask, kDynamicShadowMapResolution,""",
        "bounded compact-caster receiver resolve",
    )

    if "naviLightDirection" in text[text.find("void Interpreter::RenderShadowVolumes() {") :]:
        raise RuntimeError("stable individual light direction: stale naviLightDirection reference remains")

    path.write_text(text, encoding="utf-8", newline="\n")


def patch_dx11_receiver_shader(root: Path) -> None:
    path = root / "libultraship/src/fast/backends/gfx_direct3d11_shadow_map.inc"
    text = path.read_text(encoding="utf-8")

    old_shader = '''    const bool casterReceiverOnly = localLight.w <= -4096.0;
    float receiverFade = 1.0;
    if (casterReceiverOnly) {
        // Keep the caster body excluded, but fade the cutoff over a short vertical band so a platform edge cannot
        // reveal a hard rectangular boundary in the projected actor shadow.
        const float receiverFadeHeight = 12.0;
        receiverFade = saturate((localLight.x + receiverFadeHeight - world.y) / receiverFadeHeight);
        if (receiverFade <= 0.001) discard;
    }

    float localFill = 0.0;
    const float localRadius = casterReceiverOnly ? 0.0 : abs(localLight.w);
    if (localRadius > 0.0) {
        localFill = saturate(1.0 - length(world.xyz - localLight.xyz) / localRadius);
        localFill = localFill * localFill * (3.0 - 2.0 * localFill);
    }
    const float localFillStrength = (!casterReceiverOnly && localLight.w < 0.0) ? 0.35 : 0.85;
    const float localShadowScale = 1.0 - localFill * localFillStrength;
    const float resolvedShadowAlpha =
        saturate(shadowParams.x * shadow * edgeFade * localShadowScale * receiverFade);
    // Zero-alpha fragments must be discarded; otherwise they would still write stencil and incorrectly block a
    // later layer that contains a real shadow at the same pixel.
    if (resolvedShadowAlpha <= 0.01) discard;
    return float4(0.0, 0.0, 0.0, resolvedShadowAlpha);'''

    new_shader = '''    const bool casterReceiverOnly = localLight.w <= -4096.0;
    float receiverFade = 1.0;
    if (casterReceiverOnly) {
        // Compact actor shadows may land only on a narrow ground band around that actor. This rejects the actor's
        // own body and the vertical side of a platform while preserving the horizontal top surface beneath it.
        const float receiverFloorY = localLight.x;
        const float2 receiverCenter = float2(localLight.y, localLight.z);
        const float receiverRadius = max(24.0, -localLight.w - 8192.0);
        const float aboveFloor = world.y - receiverFloorY;
        const float belowFloor = receiverFloorY - world.y;
        const float maximumAboveFloor = 3.0;
        const float maximumBelowFloor = 24.0;
        if (aboveFloor > maximumAboveFloor || belowFloor > maximumBelowFloor) discard;

        const float verticalFade = aboveFloor > 0.0
                                       ? saturate(1.0 - aboveFloor / maximumAboveFloor)
                                       : saturate(1.0 - belowFloor / maximumBelowFloor);
        const float receiverDistance = length(world.xz - receiverCenter);
        if (receiverDistance >= receiverRadius) discard;
        const float radialFade = saturate((receiverRadius - receiverDistance) / 12.0);
        receiverFade = verticalFade * radialFade;
        if (receiverFade <= 0.01) discard;
    }

    float localFill = 0.0;
    const float localRadius = casterReceiverOnly ? 0.0 : abs(localLight.w);
    if (localRadius > 0.0) {
        localFill = saturate(1.0 - length(world.xyz - localLight.xyz) / localRadius);
        localFill = localFill * localFill * (3.0 - 2.0 * localFill);
    }
    const float localFillStrength = (!casterReceiverOnly && localLight.w < 0.0) ? 0.35 : 0.85;
    const float localShadowScale = 1.0 - localFill * localFillStrength;
    const float resolvedShadowAlpha =
        saturate(shadowParams.x * shadow * edgeFade * localShadowScale * receiverFade);
    // Zero-alpha fragments must be discarded; otherwise they would still write stencil and incorrectly block a
    // later layer that contains a real shadow at the same pixel.
    if (resolvedShadowAlpha <= 0.01) discard;
    return float4(0.0, 0.0, 0.0, resolvedShadowAlpha);'''

    shader_matches = text.count(old_shader)
    if shader_matches != 2:
        raise RuntimeError(f"bounded compact receiver shader: expected two matches, found {shader_matches}")
    text = text.replace(old_shader, new_shader)
    path.write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    patch_game_shadow_policy(root)
    patch_interpreter_header(root)
    patch_interpreter(root)
    patch_dx11_receiver_shader(root)
    print(
        "Locked Link/compact shadow anchors to stable floor cells and restricted self-shadow receivers to ground."
    )


if __name__ == "__main__":
    main()
