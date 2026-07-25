from __future__ import annotations

import re
from pathlib import Path


def replace_regex_once(text: str, pattern: str, replacement: str, label: str, flags: int = re.DOTALL) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=flags)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return updated


def replace_between_once(text: str, start: str, end: str, replacement: str, label: str) -> str:
    start_index = text.find(start)
    if start_index < 0:
        raise RuntimeError(f"{label}: start marker not found")
    end_index = text.find(end, start_index + len(start))
    if end_index < 0:
        raise RuntimeError(f"{label}: end marker not found")
    return text[:start_index] + replacement + text[end_index:]


def patch_interpreter_header(root: Path) -> None:
    path = root / "libultraship/include/fast/interpreter.h"
    text = path.read_text(encoding="utf-8")

    if "#include <unordered_set> // stable environment-shadow cache" not in text:
        marker = "#include <unordered_map>\n"
        if marker not in text:
            raise RuntimeError("interpreter header: unordered_map include not found")
        text = text.replace(marker, marker + "#include <unordered_set> // stable environment-shadow cache\n", 1)

    if "mEnvironmentShadowCasterCache" not in text:
        marker = '''    // Visible opaque environment triangles captured before the actor pass. This reuses the normal
    // Fast3D traversal, so the room is not submitted a second time merely to build the shadow map.
    std::vector<float> mEnvironmentShadowCasterAccum;
'''
        replacement = marker + '''    // Stable world-space cache of opaque room geometry. The current frame contributes newly seen
    // triangles, but camera rotation never removes triangles that were already accepted. A quantized,
    // order-independent hash prevents the same static triangle from being stored every frame.
    std::vector<float> mEnvironmentShadowCasterCache;
    std::unordered_set<uint64_t> mEnvironmentShadowTriangleHashes;
    float mEnvironmentShadowCacheAnchor[3] = { 0.0f, 0.0f, 0.0f };
    bool mEnvironmentShadowCacheAnchorValid = false;
'''
        if marker not in text:
            raise RuntimeError("interpreter header: environment accumulator members not found")
        text = text.replace(marker, replacement, 1)

    if "largeAnchorJump" not in text:
        old_anchor = '''        if (anchor != nullptr) {
            mDynamicShadowAnchor[0] = anchor[0];
            mDynamicShadowAnchor[1] = anchor[1];
            mDynamicShadowAnchor[2] = anchor[2];
        }
'''
        new_anchor = '''        if (anchor != nullptr) {
            // A very large anchor jump normally means a scene/room transition. Do not allow cached geometry
            // from the previous scene to cast at coincident coordinates in the new one.
            if (mEnvironmentShadowCacheAnchorValid) {
                const float cacheDx = anchor[0] - mEnvironmentShadowCacheAnchor[0];
                const float cacheDy = anchor[1] - mEnvironmentShadowCacheAnchor[1];
                const float cacheDz = anchor[2] - mEnvironmentShadowCacheAnchor[2];
                const bool largeAnchorJump =
                    cacheDx * cacheDx + cacheDy * cacheDy + cacheDz * cacheDz > 4096.0f * 4096.0f;
                if (largeAnchorJump) {
                    mEnvironmentShadowCasterCache.clear();
                    mEnvironmentShadowTriangleHashes.clear();
                    mEnvironmentShadowCacheAnchorValid = false;
                }
            }
            mDynamicShadowAnchor[0] = anchor[0];
            mDynamicShadowAnchor[1] = anchor[1];
            mDynamicShadowAnchor[2] = anchor[2];
        }
'''
        if old_anchor not in text:
            raise RuntimeError("interpreter header: dynamic shadow anchor block not found")
        text = text.replace(old_anchor, new_anchor, 1)

    if "mEnvironmentShadowCasterCache.clear();" not in text[text.find("if (!enabled)") :]:
        old_disable = '''            mCaptureEnvironmentShadow = false;
            mEnvironmentShadowCasterAccum.clear();
            mShadowCasterAccum.clear();
'''
        new_disable = '''            mCaptureEnvironmentShadow = false;
            mEnvironmentShadowCasterAccum.clear();
            mEnvironmentShadowCasterCache.clear();
            mEnvironmentShadowTriangleHashes.clear();
            mEnvironmentShadowCacheAnchorValid = false;
            mShadowCasterAccum.clear();
'''
        if old_disable not in text:
            raise RuntimeError("interpreter header: shadow disable cleanup block not found")
        text = text.replace(old_disable, new_disable, 1)

    path.write_text(text, encoding="utf-8", newline="\n")


def patch_interpreter(root: Path) -> None:
    path = root / "libultraship/src/fast/interpreter.cpp"
    text = path.read_text(encoding="utf-8")

    if "#include <cmath> // dynamic-shadow validation" not in text:
        marker = "#include <math.h>\n"
        if marker not in text:
            raise RuntimeError("interpreter include insertion: #include <math.h> was not found")
        text = text.replace(
            marker,
            marker
            + "#include <algorithm> // stable environment-shadow cache\n"
            + "#include <cmath> // dynamic-shadow validation\n",
            1,
        )
    elif "#include <algorithm> // stable environment-shadow cache" not in text:
        text = text.replace(
            "#include <cmath> // dynamic-shadow validation\n",
            "#include <algorithm> // stable environment-shadow cache\n"
            "#include <cmath> // dynamic-shadow validation\n",
            1,
        )

    if "kMaxShadowTriangleEdge" not in text:
        validated_lambda = r'''        auto appendShadowTriangle = [this](float ax, float ay, float az, float bx, float by, float bz, float cx,
                                            float cy, float cz) {
            // Reject malformed replacement-model geometry before it can rasterize a triangle across the
            // complete shadow cascade. This prevents the giant diagonal/rectangular black bands seen in-game.
            if (!std::isfinite(ax) || !std::isfinite(ay) || !std::isfinite(az) || !std::isfinite(bx) ||
                !std::isfinite(by) || !std::isfinite(bz) || !std::isfinite(cx) || !std::isfinite(cy) ||
                !std::isfinite(cz)) {
                return;
            }

            constexpr float kMaxShadowCasterRadius = 1536.0f;
            constexpr float kMaxShadowCasterHeight = 3072.0f;
            constexpr float kMaxShadowTriangleEdge = 1536.0f;
            const float radiusSquared = kMaxShadowCasterRadius * kMaxShadowCasterRadius;
            const float edgeSquaredLimit = kMaxShadowTriangleEdge * kMaxShadowTriangleEdge;

            auto vertexInsideCascade = [&](float x, float y, float z) {
                const float dx = x - mDynamicShadowAnchor[0];
                const float dy = y - mDynamicShadowAnchor[1];
                const float dz = z - mDynamicShadowAnchor[2];
                return (dx * dx + dz * dz) <= radiusSquared && std::fabs(dy) <= kMaxShadowCasterHeight;
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
        };'''
        text = replace_regex_once(
            text,
            r"        auto appendShadowTriangle = \[this\]\(float ax, float ay, float az, float bx, float by, float bz, float cx,\s*\n\s*float cy, float cz\) \{.*?\n        \};",
            validated_lambda,
            "shadow triangle validation",
        )

    if "The first actor marker closes room-only environment capture" not in text:
        marker = '''    memcpy(&sizeOrSentinel, &w1Bits, sizeof(sizeOrSentinel));

    // Sentinel (gSPToonShadowFlush):'''
        replacement = '''    memcpy(&sizeOrSentinel, &w1Bits, sizeof(sizeOrSentinel));

    // The first actor marker closes room-only environment capture. The actual resolve may occur after all
    // opaque actors so Link can receive shadows, but actor geometry must never leak into the static-world cache.
    mCaptureEnvironmentShadow = false;

    // Sentinel (gSPToonShadowFlush):'''
        if marker not in text:
            raise RuntimeError("interpreter actor boundary: toon-shadow sentinel marker not found")
        text = text.replace(marker, replacement, 1)

    if "Post-cull duplicate environment capture intentionally removed" not in text:
        start = "    const uint32_t cycleType = mRdp->other_mode_h & (3U << G_MDSFT_CYCLETYPE);\n"
        end = "    if (use_alpha) {\n"
        replacement = '''    // Post-cull duplicate environment capture intentionally removed. The guarded capture above runs
    // before camera rejection and the persistent cache deduplicates it across frames.

'''
        text = replace_between_once(text, start, end, replacement, "remove duplicate environment capture")

    stable_render = r'''void Interpreter::RenderShadowVolumes() {
    // Room capture ends at the first actor marker, while resolve happens here after the opaque actor pass.
    // This ordering lets Link receive world shadows without allowing actors to contaminate the static cache.
    mCaptureEnvironmentShadow = false;

    auto hashVertex = [](float x, float y, float z) {
        const int64_t qx = static_cast<int64_t>(llroundf(x * 4.0f));
        const int64_t qy = static_cast<int64_t>(llroundf(y * 4.0f));
        const int64_t qz = static_cast<int64_t>(llroundf(z * 4.0f));
        uint64_t hash = 1469598103934665603ULL;
        auto mix = [&](uint64_t value) {
            hash ^= value;
            hash *= 1099511628211ULL;
        };
        mix(static_cast<uint64_t>(qx));
        mix(static_cast<uint64_t>(qy));
        mix(static_cast<uint64_t>(qz));
        return hash;
    };

    auto hashTriangle = [&](const float* triangle) {
        uint64_t vertices[3] = {
            hashVertex(triangle[0], triangle[1], triangle[2]),
            hashVertex(triangle[3], triangle[4], triangle[5]),
            hashVertex(triangle[6], triangle[7], triangle[8]),
        };
        std::sort(vertices, vertices + 3);
        uint64_t hash = 1469598103934665603ULL;
        for (uint64_t vertex : vertices) {
            hash ^= vertex;
            hash *= 1099511628211ULL;
        }
        return hash;
    };

    auto environmentTriangleValid = [&](const float* triangle) {
        for (int i = 0; i < 9; ++i) {
            if (!std::isfinite(triangle[i])) {
                return false;
            }
        }

        const float centerX = (triangle[0] + triangle[3] + triangle[6]) / 3.0f;
        const float centerY = (triangle[1] + triangle[4] + triangle[7]) / 3.0f;
        const float centerZ = (triangle[2] + triangle[5] + triangle[8]) / 3.0f;
        const float anchorDx = centerX - mDynamicShadowAnchor[0];
        const float anchorDy = centerY - mDynamicShadowAnchor[1];
        const float anchorDz = centerZ - mDynamicShadowAnchor[2];
        if (anchorDx * anchorDx + anchorDz * anchorDz > kEnvironmentShadowCaptureRadiusSq ||
            std::fabs(anchorDy) > 3072.0f) {
            return false;
        }

        auto edgeLengthSquared = [](const float* a, const float* b) {
            const float dx = b[0] - a[0];
            const float dy = b[1] - a[1];
            const float dz = b[2] - a[2];
            return dx * dx + dy * dy + dz * dz;
        };
        constexpr float kMaximumStaticEdge = 4096.0f;
        const float maximumStaticEdgeSquared = kMaximumStaticEdge * kMaximumStaticEdge;
        if (edgeLengthSquared(&triangle[0], &triangle[3]) > maximumStaticEdgeSquared ||
            edgeLengthSquared(&triangle[3], &triangle[6]) > maximumStaticEdgeSquared ||
            edgeLengthSquared(&triangle[6], &triangle[0]) > maximumStaticEdgeSquared) {
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
        return crossX * crossX + crossY * crossY + crossZ * crossZ >= 0.0001f;
    };

    // Recenter lazily as Link travels. Existing cached triangles that still overlap the new capture radius
    // survive, so crossing a cache cell cannot make all world shadows disappear for one frame.
    const float cacheDx = mDynamicShadowAnchor[0] - mEnvironmentShadowCacheAnchor[0];
    const float cacheDy = mDynamicShadowAnchor[1] - mEnvironmentShadowCacheAnchor[1];
    const float cacheDz = mDynamicShadowAnchor[2] - mEnvironmentShadowCacheAnchor[2];
    const bool recenterCache = !mEnvironmentShadowCacheAnchorValid ||
                               cacheDx * cacheDx + cacheDz * cacheDz > 384.0f * 384.0f ||
                               std::fabs(cacheDy) > 1024.0f;
    if (recenterCache) {
        std::vector<float> retained;
        retained.reserve(mEnvironmentShadowCasterCache.size());
        mEnvironmentShadowTriangleHashes.clear();
        for (size_t offset = 0; offset + 8 < mEnvironmentShadowCasterCache.size(); offset += 9) {
            const float* triangle = &mEnvironmentShadowCasterCache[offset];
            if (!environmentTriangleValid(triangle)) {
                continue;
            }
            const uint64_t hash = hashTriangle(triangle);
            if (mEnvironmentShadowTriangleHashes.insert(hash).second &&
                retained.size() + 9 <= kEnvironmentShadowBudgetFloats) {
                retained.insert(retained.end(), triangle, triangle + 9);
            }
        }
        mEnvironmentShadowCasterCache.swap(retained);
        mEnvironmentShadowCacheAnchor[0] = mDynamicShadowAnchor[0];
        mEnvironmentShadowCacheAnchor[1] = mDynamicShadowAnchor[1];
        mEnvironmentShadowCacheAnchor[2] = mDynamicShadowAnchor[2];
        mEnvironmentShadowCacheAnchorValid = true;
    }

    // Merge this frame's room traversal into the persistent cache. Camera rotation can add newly discovered
    // geometry, but it cannot remove already cached geometry, eliminating camera-dependent shadow popping.
    for (size_t offset = 0; offset + 8 < mEnvironmentShadowCasterAccum.size(); offset += 9) {
        const float* triangle = &mEnvironmentShadowCasterAccum[offset];
        if (!environmentTriangleValid(triangle)) {
            continue;
        }
        const uint64_t hash = hashTriangle(triangle);
        if (mEnvironmentShadowTriangleHashes.insert(hash).second &&
            mEnvironmentShadowCasterCache.size() + 9 <= kEnvironmentShadowBudgetFloats) {
            mEnvironmentShadowCasterCache.insert(mEnvironmentShadowCasterCache.end(), triangle, triangle + 9);
        }
    }
    mEnvironmentShadowCasterAccum.clear();

    // Temporarily append dynamic actor casters to the stable world cache for this resolve only.
    const size_t stableEnvironmentFloats = mEnvironmentShadowCasterCache.size();
    const size_t available = stableEnvironmentFloats < kShadowAccumBudgetFloats
                                 ? kShadowAccumBudgetFloats - stableEnvironmentFloats
                                 : 0;
    const size_t actorFloats = std::min(mShadowCasterAccum.size(), available - (available % 9));
    if (actorFloats >= 9) {
        mEnvironmentShadowCasterCache.insert(mEnvironmentShadowCasterCache.end(), mShadowCasterAccum.begin(),
                                             mShadowCasterAccum.begin() + actorFloats);
    }

    // Fast3D applies its widescreen correction to clip X after P_matrix. Pass the exact effective matrix so
    // world reconstruction from the DX11 depth buffer remains stable while the camera rotates.
    float effectiveCamera[16];
    memcpy(effectiveCamera, &mRsp->P_matrix[0][0], sizeof(effectiveCamera));
    if (!mFbActive && mCurDimensions.width > 0 && mCurDimensions.height > 0) {
        const float targetAspect = static_cast<float>(mCurDimensions.width) / static_cast<float>(mCurDimensions.height);
        const float aspectScale = (4.0f / 3.0f) / targetAspect;
        for (int row = 0; row < 4; row++) {
            effectiveCamera[row * 4] *= aspectScale;
        }
    }

    const size_t vertexCount = mEnvironmentShadowCasterCache.size() / 3;
    mRapi->RenderDynamicShadowMap(
        vertexCount >= 3 ? mEnvironmentShadowCasterCache.data() : nullptr, vertexCount, effectiveCamera,
        mDynamicShadowLightDir, mDynamicShadowAnchor, mDynamicShadowLocalLight, kDynamicShadowMapResolution,
        std::clamp(mToonShadowAlpha, 0.0f, 1.0f), kDynamicShadowMapBias, kDynamicShadowMapPcfRadius);

    mEnvironmentShadowCasterCache.resize(stableEnvironmentFloats);
    mShadowCasterAccum.clear();

    for (int band = 0; band < kShadowBands; band++) {
        mShadowVolumeAccum[band].clear();
        mShadowVolumeKind[band].clear();
    }
}
'''

    render_start = "void Interpreter::RenderShadowVolumes() {"
    render_end = "void Interpreter::GfxDpSetGrayscaleColor"
    text = replace_between_once(text, render_start, render_end, stable_render + "\n", "stable world shadow cache")

    path.write_text(text, encoding="utf-8", newline="\n")


def patch_toon_lighting(root: Path) -> None:
    path = root / "soh/soh/Enhancements/Graphics/ToonLighting.cpp"
    text = path.read_text(encoding="utf-8")

    if "ZZNaviShadowLightFix.cpp is the only owner of the dynamic shadow-map state" not in text:
        text = replace_regex_once(
            text,
            r"    // Dynamic shadow mapping.*?\n    // Clear before any early-out",
            r'''    // ZZNaviShadowLightFix.cpp is the only owner of the dynamic shadow-map state. Keeping a second
    // SetDynamicShadowCaptureState call here made two unordered frame hooks compete over direction and Navi
    // state, producing frame-to-frame instability.

    // Clear before any early-out''',
            "single shadow-map state owner",
        )

    if "ZZNaviShadowLightFix.cpp exclusively owns caster arming" not in text:
        text = replace_regex_once(
            text,
            r"    // Actor shadow: arm this actor as a dynamic shadow-map caster\..*?\n    if \(sParams\.showDebug\)",
            r'''    // ZZNaviShadowLightFix.cpp exclusively owns caster arming and world-space range hysteresis.
    // The duplicate camera/frustum-dependent arming path that used to live here was intentionally removed.

    if (sParams.showDebug)''',
            "single shadow caster policy",
        )

    path.write_text(text, encoding="utf-8", newline="\n")


def patch_actor_draw_order(root: Path) -> None:
    path = root / "soh/src/code/z_actor.c"
    text = path.read_text(encoding="utf-8")

    if "Navi's pool was the large circular ball on the ground" not in text:
        text = replace_regex_once(
            text,
            r"^[ \t]*GameInteractor_ExecuteOnPlayDrawWorldLights\(play\);[ \t]*$",
            r'''    // Point lights, including Navi, continue to affect actor toon lighting through ToonLighting.cpp.
    // Do not draw the old world-space light pool: Navi's pool was the large circular ball on the ground.''',
            "remove Navi ground light pool",
            flags=re.MULTILINE,
        )

    if "Resolve the shadow map after opaque actors" not in text:
        text = replace_regex_once(
            text,
            r"\n    if \(shadowsEnabled\) \{\n        gSPToonShadowFlush\(POLY_OPA_DISP\+\+\);\n    \}\n\n    actorListEntry = &actorCtx->actorLists\[0\];",
            "\n    actorListEntry = &actorCtx->actorLists[0];",
            "remove pre-actor shadow resolve",
        )

        text = replace_regex_once(
            text,
            r"    \}\n\n    // SOH \[Enhancement\] Toon lighting: end the actor bracket before effects/lens/UI are drawn\.",
            r'''    }

    if (shadowsEnabled) {
        // Resolve the shadow map after opaque actors have written depth. Link and other solid actors now receive
        // cast shadows. Navi remains in the later translucent stream and is never submitted as a caster.
        gSPToonShadowFlush(POLY_OPA_DISP++);
    }

    // SOH [Enhancement] Toon lighting: end the actor bracket before effects/lens/UI are drawn.''',
            "post-actor shadow resolve",
        )

    path.write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    patch_interpreter_header(root)
    patch_interpreter(root)
    patch_toon_lighting(root)
    patch_actor_draw_order(root)
    print(
        "Applied stable cached world and actor shadows, invalid-triangle rejection, post-actor receiving, "
        "and removed Navi's ground light pool."
    )


if __name__ == "__main__":
    main()
