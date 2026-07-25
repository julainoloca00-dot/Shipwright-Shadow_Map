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
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return updated


def patch_navi_visibility(root: Path) -> None:
    path = root / "soh/soh/Enhancements/Graphics/ZZNaviShadowLightFix.cpp"
    text = path.read_text(encoding="utf-8")

    old_state = '''int sPendingDirectionFrames = 0;'''
    new_state = '''int sPendingDirectionFrames = 0;
int sLastShadowScene = -1;
int sLastShadowRoom = -128;'''
    text = replace_once(text, old_state, new_state, "shadow world-context state")

    old_disabled = '''        sStableShadowStates.clear();
        sShadowDirectionValid = false;
        sPendingDirectionFrames = 0;
        return;'''
    new_disabled = '''        sStableShadowStates.clear();
        sShadowDirectionValid = false;
        sPendingDirectionFrames = 0;
        sLastShadowScene = -1;
        sLastShadowRoom = -128;
        return;'''
    text = replace_once(text, old_disabled, new_disabled, "shadow disabled context reset")

    old_play_start = '''    if (gPlayState != nullptr) {
        float targetDirection[3] = { kDefaultDirection[0], kDefaultDirection[1], kDefaultDirection[2] };'''
    new_play_start = '''    if (gPlayState != nullptr) {
        // The persistent room cache must never survive a scene or room transition. Old room triangles can be
        // perfectly finite yet occupy the same coordinates in the new room, producing a giant black strip.
        const int currentScene = static_cast<int>(gPlayState->sceneNum);
        const int currentRoom = static_cast<int>(gPlayState->roomCtx.curRoom.num);
        if (currentScene != sLastShadowScene || currentRoom != sLastShadowRoom) {
            interpreter->SetDynamicShadowCaptureState(false, sStableShadowDirection, anchor, localShadowLight);
            sStableShadowStates.clear();
            sShadowDirectionValid = false;
            sPendingDirectionFrames = 0;
            sEnvironmentInitialized = false;
            sLastShadowScene = currentScene;
            sLastShadowRoom = currentRoom;
        }

        float targetDirection[3] = { kDefaultDirection[0], kDefaultDirection[1], kDefaultDirection[2] };'''
    text = replace_once(text, old_play_start, new_play_start, "scene and room shadow-cache reset")

    old_navi = '''            Actor* naviActor = player->naviActor;
            const bool naviLightcastingEnabled =
                CVarGetInteger(CVAR_ENHANCEMENT("Graphics.ToonLighting.UseNaviLight"), 1) != 0;
            if (naviLightcastingEnabled && naviActor != nullptr && naviActor->id == ACTOR_EN_ELF &&
                naviActor->params == FAIRY_NAVI) {
                localShadowLight[0] = naviActor->world.pos.x;
                localShadowLight[1] = naviActor->world.pos.y;
                localShadowLight[2] = naviActor->world.pos.z;
                localShadowLight[3] = kNaviShadowFillRadius;
            }'''
    new_navi = '''            Actor* naviActor = player->naviActor;
            const bool naviLightcastingEnabled =
                CVarGetInteger(CVAR_ENHANCEMENT("Graphics.ToonLighting.UseNaviLight"), 1) != 0;
            if (naviLightcastingEnabled && naviActor != nullptr && naviActor->id == ACTOR_EN_ELF &&
                naviActor->params == FAIRY_NAVI) {
                EnElf* navi = reinterpret_cast<EnElf*>(naviActor);
                const float visualScale = std::clamp(navi->actor.scale.x / 0.008f, 0.0f, 1.0f);
                const float emittedRadius =
                    std::clamp(static_cast<float>(navi->lightInfoGlow.params.point.radius) / 100.0f, 0.0f, 1.0f);
                const bool visiblyEmitting = navi->unk_2A8 != 8 && (navi->fairyFlags & 8) == 0 &&
                                              visualScale > 0.01f && emittedRadius > 0.01f;
                if (visiblyEmitting) {
                    // Use the same state that drives EnElf_Draw and EnElf_UpdateLights. During Navi's vanish
                    // animation the shadow fill fades with her instead of remaining active as an invisible light.
                    const float visibleEmission = visualScale * emittedRadius;
                    localShadowLight[0] = naviActor->world.pos.x;
                    localShadowLight[1] = naviActor->world.pos.y;
                    localShadowLight[2] = naviActor->world.pos.z;
                    localShadowLight[3] = kNaviShadowFillRadius * visibleEmission;
                }
            }'''
    text = replace_once(text, old_navi, new_navi, "visible Navi shadow fill")

    path.write_text(text, encoding="utf-8", newline="\n")


def patch_triangle_validation(root: Path) -> None:
    path = root / "libultraship/src/fast/interpreter.cpp"
    text = path.read_text(encoding="utf-8")

    old_actor_edges = '''            if (edgeLengthSquared(ax, ay, az, bx, by, bz) > edgeSquaredLimit ||
                edgeLengthSquared(bx, by, bz, cx, cy, cz) > edgeSquaredLimit ||
                edgeLengthSquared(cx, cy, cz, ax, ay, az) > edgeSquaredLimit) {
                return;
            }'''
    new_actor_edges = '''            const float abEdgeSquared = edgeLengthSquared(ax, ay, az, bx, by, bz);
            const float bcEdgeSquared = edgeLengthSquared(bx, by, bz, cx, cy, cz);
            const float caEdgeSquared = edgeLengthSquared(cx, cy, cz, ax, ay, az);
            if (abEdgeSquared > edgeSquaredLimit || bcEdgeSquared > edgeSquaredLimit ||
                caEdgeSquared > edgeSquaredLimit) {
                return;
            }
            const float shortestEdgeSquared = std::min(abEdgeSquared, std::min(bcEdgeSquared, caEdgeSquared));
            const float longestEdgeSquared = std::max(abEdgeSquared, std::max(bcEdgeSquared, caEdgeSquared));
            if (shortestEdgeSquared < 0.0001f ||
                (longestEdgeSquared > 256.0f * 256.0f && longestEdgeSquared > shortestEdgeSquared * 256.0f)) {
                // Extremely thin stretched triangles are normally corrupted replacement-model geometry. In a
                // depth-only map they become the long black bands that cross most of the screen.
                return;
            }'''
    text = replace_once(text, old_actor_edges, new_actor_edges, "actor stretched-triangle rejection")

    environment_pattern = r'''        constexpr float kMaximumStaticEdge = 4096\.0f;\n        const float maximumStaticEdgeSquared = kMaximumStaticEdge \* kMaximumStaticEdge;\n        if \(edgeLengthSquared\(&triangle\[0\], &triangle\[3\]\) > maximumStaticEdgeSquared \|\|\n            edgeLengthSquared\(&triangle\[3\], &triangle\[6\]\) > maximumStaticEdgeSquared \|\|\n            edgeLengthSquared\(&triangle\[6\], &triangle\[0\]\) > maximumStaticEdgeSquared\) \{\n            return false;\n        \}\n\n        const float abx = triangle\[3\] - triangle\[0\];\n        const float aby = triangle\[4\] - triangle\[1\];\n        const float abz = triangle\[5\] - triangle\[2\];\n        const float acx = triangle\[6\] - triangle\[0\];\n        const float acy = triangle\[7\] - triangle\[1\];\n        const float acz = triangle\[8\] - triangle\[2\];\n        const float crossX = aby \* acz - abz \* acy;\n        const float crossY = abz \* acx - abx \* acz;\n        const float crossZ = abx \* acy - aby \* acx;\n        return crossX \* crossX \+ crossY \* crossY \+ crossZ \* crossZ >= 0\.0001f;'''
    environment_replacement = '''        constexpr float kMaximumStaticEdge = 1536.0f;
        const float maximumStaticEdgeSquared = kMaximumStaticEdge * kMaximumStaticEdge;
        const float abEdgeSquared = edgeLengthSquared(&triangle[0], &triangle[3]);
        const float bcEdgeSquared = edgeLengthSquared(&triangle[3], &triangle[6]);
        const float caEdgeSquared = edgeLengthSquared(&triangle[6], &triangle[0]);
        if (abEdgeSquared > maximumStaticEdgeSquared || bcEdgeSquared > maximumStaticEdgeSquared ||
            caEdgeSquared > maximumStaticEdgeSquared) {
            return false;
        }
        const float shortestEdgeSquared = std::min(abEdgeSquared, std::min(bcEdgeSquared, caEdgeSquared));
        const float longestEdgeSquared = std::max(abEdgeSquared, std::max(bcEdgeSquared, caEdgeSquared));
        if (shortestEdgeSquared < 0.0001f ||
            (longestEdgeSquared > 256.0f * 256.0f && longestEdgeSquared > shortestEdgeSquared * 256.0f)) {
            return false;
        }

        const float abx = triangle[3] - triangle[0];
        const float aby = triangle[4] - triangle[1];
        const float abz = triangle[5] - triangle[2];
        const float acx = triangle[6] - triangle[0];
        const float acy = triangle[7] - triangle[1];
        const float acz = triangle[8] - triangle[2];
        const float crossX = aby * acz - abz * acy;
        const float crossY = abz * acx - abx * acz;
        const float crossZ = abx * acy - aby * acx;
        const float crossLengthSquared = crossX * crossX + crossY * crossY + crossZ * crossZ;
        if (crossLengthSquared < 0.0001f) {
            return false;
        }

        // Do not cache the current ground/floor as a caster. A huge floor triangle is also the receiver and can
        // self-shadow into a rectangular band despite depth bias. Horizontal roofs/platforms above Link remain.
        const float absoluteNormalY = std::fabs(crossY) / std::sqrt(crossLengthSquared);
        if (absoluteNormalY > 0.92f && centerY <= mDynamicShadowAnchor[1] + 96.0f) {
            return false;
        }
        return true;'''
    text = replace_regex_once(
        text,
        environment_pattern,
        environment_replacement,
        "environment floor and stretched-triangle rejection",
    )

    path.write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    patch_navi_visibility(root)
    patch_triangle_validation(root)
    print(
        "Gated Navi shadow fill by real visual emission, reset caches on room changes, and rejected band-producing triangles."
    )


if __name__ == "__main__":
    main()
