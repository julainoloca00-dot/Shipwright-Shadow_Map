from __future__ import annotations

import re
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.DOTALL)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return updated


def patch_interpreter(root: Path) -> None:
    path = root / "libultraship/src/fast/interpreter.cpp"
    text = path.read_text(encoding="utf-8")

    old_append = '''        auto appendShadowTriangle = [this](float ax, float ay, float az, float bx, float by, float bz, float cx,
                                            float cy, float cz) {
            mShadowVerts.push_back(ax);
            mShadowVerts.push_back(ay);
            mShadowVerts.push_back(az);
            mShadowVerts.push_back(bx);
            mShadowVerts.push_back(by);
            mShadowVerts.push_back(bz);
            mShadowVerts.push_back(cx);
            mShadowVerts.push_back(cy);
            mShadowVerts.push_back(cz);
        };
'''
    new_append = '''        auto appendShadowTriangle = [this](float ax, float ay, float az, float bx, float by, float bz, float cx,
                                            float cy, float cz) {
            // A stale matrix, an unclosed actor stream or malformed replacement model can produce one vertex
            // thousands of units away from the other two. Rasterizing that triangle creates the giant diagonal
            // black bands seen across the whole scene. Reject non-finite, degenerate, overlong and out-of-cascade
            // triangles before they ever enter the shared shadow buffer.
            if (!std::isfinite(ax) || !std::isfinite(ay) || !std::isfinite(az) || !std::isfinite(bx) ||
                !std::isfinite(by) || !std::isfinite(bz) || !std::isfinite(cx) || !std::isfinite(cy) ||
                !std::isfinite(cz)) {
                return;
            }

            constexpr float kMaxCasterRadius = 1536.0f;
            constexpr float kMaxCasterHeight = 3072.0f;
            constexpr float kMaxTriangleEdge = 1536.0f;
            const float radiusSquared = kMaxCasterRadius * kMaxCasterRadius;
            const float edgeSquaredLimit = kMaxTriangleEdge * kMaxTriangleEdge;

            auto vertexInsideCascade = [&](float x, float y, float z) {
                const float dx = x - mDynamicShadowAnchor[0];
                const float dy = y - mDynamicShadowAnchor[1];
                const float dz = z - mDynamicShadowAnchor[2];
                return (dx * dx + dz * dz) <= radiusSquared && std::fabs(dy) <= kMaxCasterHeight;
            };
            if (!vertexInsideCascade(ax, ay, az) || !vertexInsideCascade(bx, by, bz) ||
                !vertexInsideCascade(cx, cy, cz)) {
                return;
            }

            auto edgeLengthSquared = [](float x0, float y0, float z0, float x1, float y1, float z1) {
                const float dx = x1 - x0;
                const float dy = y1 - y0;
                const float dz = z1 - z0;
                return dx * dx + dy * dy + dz * dz;
            };
            if (edgeLengthSquared(ax, ay, az, bx, by, bz) > edgeSquaredLimit ||
                edgeLengthSquared(bx, by, bz, cx, cy, cz) > edgeSquaredLimit ||
                edgeLengthSquared(cx, cy, cz, ax, ay, az) > edgeSquaredLimit) {
                return;
            }

            const float abx = bx - ax;
            const float aby = by - ay;
            const float abz = bz - az;
            const float acx = cx - ax;
            const float acy = cy - ay;
            const float acz = cz - az;
            const float crossX = aby * acz - abz * acy;
            const float crossY = abz * acx - abx * acz;
            const float crossZ = abx * acy - aby * acx;
            if (crossX * crossX + crossY * crossY + crossZ * crossZ < 0.0001f) {
                return;
            }

            mShadowVerts.push_back(ax);
            mShadowVerts.push_back(ay);
            mShadowVerts.push_back(az);
            mShadowVerts.push_back(bx);
            mShadowVerts.push_back(by);
            mShadowVerts.push_back(bz);
            mShadowVerts.push_back(cx);
            mShadowVerts.push_back(cy);
            mShadowVerts.push_back(cz);
        };
'''
    if old_append in text:
        text = replace_once(text, old_append, new_append, "shadow triangle validation")
    elif "kMaxTriangleEdge" not in text:
        raise RuntimeError("shadow triangle validation: alpha-aware append lambda not found")

    old_render = '''    // This command is emitted after the opaque room and before normal actors. Stop room capture here,
    // then combine this frame's visible environment with the previous frame's accepted actor casters.
    mCaptureEnvironmentShadow = false;

    const size_t available = mEnvironmentShadowCasterAccum.size() < kShadowAccumBudgetFloats
                                 ? kShadowAccumBudgetFloats - mEnvironmentShadowCasterAccum.size()
                                 : 0;
    const size_t copyFloats = std::min(mShadowCasterAccum.size(), available - (available % 9));
    if (copyFloats >= 9) {
        mEnvironmentShadowCasterAccum.insert(mEnvironmentShadowCasterAccum.end(), mShadowCasterAccum.begin(),
                                             mShadowCasterAccum.begin() + copyFloats);
    }

    // Fast3D applies its widescreen correction to clip X after P_matrix. Pass the exact effective
    // matrix to the backend so reconstruction from the DX11 depth buffer cannot swim with camera rotation.
    float effectiveCamera[16];
    memcpy(effectiveCamera, &mRsp->P_matrix[0][0], sizeof(effectiveCamera));
    if (!mFbActive && mCurDimensions.width > 0 && mCurDimensions.height > 0) {
        const float targetAspect = static_cast<float>(mCurDimensions.width) / static_cast<float>(mCurDimensions.height);
        const float aspectScale = (4.0f / 3.0f) / targetAspect;
        for (int row = 0; row < 4; row++) {
            effectiveCamera[row * 4] *= aspectScale;
        }
    }

    const size_t vertexCount = mEnvironmentShadowCasterAccum.size() / 3;
    mRapi->RenderDynamicShadowMap(
        vertexCount >= 3 ? mEnvironmentShadowCasterAccum.data() : nullptr, vertexCount, effectiveCamera,
        mDynamicShadowLightDir, mDynamicShadowAnchor, mDynamicShadowLocalLight, kDynamicShadowMapResolution,
        std::clamp(mToonShadowAlpha, 0.0f, 1.0f), kDynamicShadowMapBias, kDynamicShadowMapPcfRadius);

    mEnvironmentShadowCasterAccum.clear();
    mShadowCasterAccum.clear();
'''
    new_render = '''    // Finish room capture, but do not use camera-visible room triangles as shadow casters. Their set changes
    // whenever the camera turns, which made large terrain shadows appear, disappear or form diagonal bands.
    // The map now contains only explicitly armed actor geometry, selected in stable world space around Link.
    mCaptureEnvironmentShadow = false;

    // Fast3D applies its widescreen correction to clip X after P_matrix. Pass the exact effective
    // matrix to the backend so reconstruction from the DX11 depth buffer cannot swim with camera rotation.
    float effectiveCamera[16];
    memcpy(effectiveCamera, &mRsp->P_matrix[0][0], sizeof(effectiveCamera));
    if (!mFbActive && mCurDimensions.width > 0 && mCurDimensions.height > 0) {
        const float targetAspect = static_cast<float>(mCurDimensions.width) / static_cast<float>(mCurDimensions.height);
        const float aspectScale = (4.0f / 3.0f) / targetAspect;
        for (int row = 0; row < 4; row++) {
            effectiveCamera[row * 4] *= aspectScale;
        }
    }

    const size_t vertexCount = mShadowCasterAccum.size() / 3;
    mRapi->RenderDynamicShadowMap(vertexCount >= 3 ? mShadowCasterAccum.data() : nullptr, vertexCount,
                                  effectiveCamera, mDynamicShadowLightDir, mDynamicShadowAnchor,
                                  mDynamicShadowLocalLight, kDynamicShadowMapResolution,
                                  std::clamp(mToonShadowAlpha, 0.0f, 1.0f), kDynamicShadowMapBias,
                                  kDynamicShadowMapPcfRadius);

    mEnvironmentShadowCasterAccum.clear();
    mShadowCasterAccum.clear();
'''
    if old_render in text:
        text = replace_once(text, old_render, new_render, "actor-only stable shadow map")
    elif "The map now contains only explicitly armed actor geometry" not in text:
        raise RuntimeError("actor-only stable shadow map: RenderShadowVolumes block not found")

    path.write_text(text, encoding="utf-8", newline="\n")


def patch_toon_lighting(root: Path) -> None:
    path = root / "soh/soh/Enhancements/Graphics/ToonLighting.cpp"
    text = path.read_text(encoding="utf-8")

    if "ZZNaviShadowLightFix.cpp is the only owner of the dynamic shadow-map state" not in text:
        text = regex_once(
            text,
            r"    // Dynamic shadow mapping uses one stable.*?\n    // Clear before any early-out",
            '''    // ZZNaviShadowLightFix.cpp is the only owner of the dynamic shadow-map state. Keeping a second
    // SetDynamicShadowCaptureState call here made the environment direction and the Navi-derived direction
    // compete in an unordered hook list, producing frame-to-frame instability.

    // Clear before any early-out''',
            "single shadow-map state owner",
        )

    if "ZZNaviShadowLightFix.cpp exclusively owns caster arming" not in text:
        text = regex_once(
            text,
            r"    // Actor shadow: arm this actor as a dynamic shadow-map caster\..*?\n    if \(sParams\.showDebug\)",
            '''    // ZZNaviShadowLightFix.cpp exclusively owns caster arming and world-space range hysteresis.
    // The duplicate camera/frustum-dependent arming path that used to live here was intentionally removed.

    if (sParams.showDebug)''',
            "single shadow caster policy",
        )

    path.write_text(text, encoding="utf-8", newline="\n")


def patch_actor_draw_order(root: Path) -> None:
    path = root / "soh/src/code/z_actor.c"
    text = path.read_text(encoding="utf-8")

    old_world_lights = "    GameInteractor_ExecuteOnPlayDrawWorldLights(play);\n"
    new_world_lights = '''    // Point lights, including Navi, continue to affect actor toon lighting through ToonLighting.cpp.
    // Do not draw the old world-space light pool: Navi's pool was the large circular ball on the ground.
'''
    if old_world_lights in text:
        text = replace_once(text, old_world_lights, new_world_lights, "remove Navi ground light pool")
    elif "Navi's pool was the large circular ball on the ground" not in text:
        raise RuntimeError("remove Navi ground light pool: world-light hook call not found")

    old_pre_actor_flush = '''    if (shadowsEnabled) {
        gSPToonShadowFlush(POLY_OPA_DISP++);
    }

    actorListEntry = &actorCtx->actorLists[0];
'''
    new_pre_actor_flush = '''    actorListEntry = &actorCtx->actorLists[0];
'''
    if old_pre_actor_flush in text:
        text = replace_once(text, old_pre_actor_flush, new_pre_actor_flush, "remove pre-actor shadow resolve")
    elif "Resolve the shadow map after opaque actors" not in text:
        raise RuntimeError("remove pre-actor shadow resolve: flush block not found")

    old_post_loop = '''    }

    // SOH [Enhancement] Toon lighting: end the actor bracket before effects/lens/UI are drawn.
'''
    new_post_loop = '''    }

    if (shadowsEnabled) {
        // Resolve the shadow map after opaque actors have written depth. Link and other solid actors now receive
        // cast shadows instead of only the room receiving them. Navi remains in the later translucent stream and
        // is never submitted as a caster by the stable actor policy.
        gSPToonShadowFlush(POLY_OPA_DISP++);
    }

    // SOH [Enhancement] Toon lighting: end the actor bracket before effects/lens/UI are drawn.
'''
    if old_post_loop in text:
        text = replace_once(text, old_post_loop, new_post_loop, "post-actor shadow resolve")
    elif "Resolve the shadow map after opaque actors" not in text:
        raise RuntimeError("post-actor shadow resolve: actor-loop boundary not found")

    path.write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    patch_interpreter(root)
    patch_toon_lighting(root)
    patch_actor_draw_order(root)
    print(
        "Applied actor-only camera-independent shadows, invalid-triangle rejection, post-actor receiving, "
        "and removed Navi's ground light pool."
    )


if __name__ == "__main__":
    main()
