from __future__ import annotations

from pathlib import Path


def insert_after_once(text: str, marker: str, addition: str, label: str) -> str:
    index = text.find(marker)
    if index < 0:
        raise RuntimeError(f"{label}: marker not found")
    return text[: index + len(marker)] + addition + text[index + len(marker) :]


def replace_between(text: str, start_marker: str, end_marker: str, replacement: str, label: str) -> str:
    start = text.find(start_marker)
    if start < 0:
        raise RuntimeError(f"{label}: start marker not found")
    end = text.find(end_marker, start + len(start_marker))
    if end < 0:
        raise RuntimeError(f"{label}: end marker not found")
    return text[:start] + replacement + text[end:]


def patch_interpreter_header(root: Path) -> None:
    path = root / "libultraship/include/fast/interpreter.h"
    text = path.read_text(encoding="utf-8")

    if "struct NaviShadowCasterBatch" not in text:
        marker = "    std::vector<float> mShadowCasterAccum;"
        addition = '''
    // Each compact caster affected by Navi keeps its own geometry range and centre. A shared average centre
    // produced incorrect angles and stretched shadows whenever Link and another actor were affected together.
    struct NaviShadowCasterBatch {
        size_t firstFloat = 0;
        size_t floatCount = 0;
        float center[3] = { 0.0f, 0.0f, 0.0f };
    };
    std::vector<float> mNaviShadowCasterAccum;
    std::vector<NaviShadowCasterBatch> mNaviShadowCasterBatches;'''
        text = insert_after_once(text, marker, addition, "per-caster Navi shadow state")

    if "mNaviShadowCasterBatches.clear();" not in text:
        disabled = text.find("if (!enabled)")
        if disabled < 0:
            raise RuntimeError("per-caster Navi cleanup: disabled-state block not found")
        actor_clear = text.find("mShadowCasterAccum.clear();", disabled)
        if actor_clear < 0:
            raise RuntimeError("per-caster Navi cleanup: actor clear not found")
        line_end = text.find("\n", actor_clear)
        if line_end < 0:
            raise RuntimeError("per-caster Navi cleanup: actor clear line end not found")
        indent_start = text.rfind("\n", 0, actor_clear) + 1
        indent = text[indent_start:actor_clear]
        cleanup = (
            f"\n{indent}mNaviShadowCasterAccum.clear();"
            f"\n{indent}mNaviShadowCasterBatches.clear();"
        )
        text = text[:line_end] + cleanup + text[line_end:]

    path.write_text(text, encoding="utf-8", newline="\n")


def patch_interpreter(root: Path) -> None:
    path = root / "libultraship/src/fast/interpreter.cpp"
    text = path.read_text(encoding="utf-8")

    flush_start = "void Interpreter::FlushToonShadow() {"
    render_start = "void Interpreter::RenderShadowVolumes() {"
    flush = r'''void Interpreter::FlushToonShadow() {
    const float coreAlpha = std::clamp(mToonShadowAlpha, 0.0f, 1.0f);
    if (mShadowVerts.size() < 9 || coreAlpha <= 0.0f || !mRdp->toon_shadow) {
        mShadowVerts.clear();
        return;
    }

    // Classify the complete current object by world-space bounds. Large actors retain the environmental
    // direction. Every compact caster close to visible Navi receives its own batch, centre and direction.
    float minimum[3] = { mShadowVerts[0], mShadowVerts[1], mShadowVerts[2] };
    float maximum[3] = { minimum[0], minimum[1], minimum[2] };
    for (size_t offset = 3; offset + 2 < mShadowVerts.size(); offset += 3) {
        minimum[0] = std::min(minimum[0], mShadowVerts[offset + 0]);
        minimum[1] = std::min(minimum[1], mShadowVerts[offset + 1]);
        minimum[2] = std::min(minimum[2], mShadowVerts[offset + 2]);
        maximum[0] = std::max(maximum[0], mShadowVerts[offset + 0]);
        maximum[1] = std::max(maximum[1], mShadowVerts[offset + 1]);
        maximum[2] = std::max(maximum[2], mShadowVerts[offset + 2]);
    }

    constexpr float kLargeLocalLightCasterExtent = 240.0f;
    const bool largeCaster = maximum[0] - minimum[0] > kLargeLocalLightCasterExtent ||
                             maximum[1] - minimum[1] > kLargeLocalLightCasterExtent ||
                             maximum[2] - minimum[2] > kLargeLocalLightCasterExtent;

    const float center[3] = {
        (minimum[0] + maximum[0]) * 0.5f,
        (minimum[1] + maximum[1]) * 0.5f,
        (minimum[2] + maximum[2]) * 0.5f,
    };
    constexpr float kNaviDirectionInfluenceRadius = 220.0f;
    const float publishedRadius = std::fabs(mDynamicShadowLocalLight[3]);
    const float naviRadius = std::min(publishedRadius, kNaviDirectionInfluenceRadius);
    const float naviDx = center[0] - mDynamicShadowLocalLight[0];
    const float naviDy = center[1] - mDynamicShadowLocalLight[1];
    const float naviDz = center[2] - mDynamicShadowLocalLight[2];
    const float halfExtentX = (maximum[0] - minimum[0]) * 0.5f;
    const float halfExtentY = (maximum[1] - minimum[1]) * 0.5f;
    const float halfExtentZ = (maximum[2] - minimum[2]) * 0.5f;
    const float actorHalfExtent = std::max(halfExtentX, std::max(halfExtentY, halfExtentZ));
    const float effectiveNaviRadius = naviRadius + std::min(actorHalfExtent, 64.0f);
    const bool insideNaviRadius = naviRadius > 0.0f &&
                                  naviDx * naviDx + naviDy * naviDy + naviDz * naviDz <=
                                      effectiveNaviRadius * effectiveNaviRadius;

    // Each nearby actor requires its own projection direction. Cap the extra passes; overflow actors keep
    // the stable environmental direction rather than being merged into a geometrically incorrect average.
    constexpr size_t kMaxNaviShadowBatches = 6;
    const bool useNaviBatch =
        !largeCaster && insideNaviRadius && mNaviShadowCasterBatches.size() < kMaxNaviShadowBatches;
    std::vector<float>& destination = largeCaster
                                          ? mLargeShadowCasterAccum
                                          : (useNaviBatch ? mNaviShadowCasterAccum : mShadowCasterAccum);

    const size_t firstFloat = destination.size();
    const size_t available = destination.size() < kShadowAccumBudgetFloats
                                 ? kShadowAccumBudgetFloats - destination.size()
                                 : 0;
    const size_t copyFloats = std::min(mShadowVerts.size(), available - (available % 9));
    if (copyFloats >= 9) {
        destination.insert(destination.end(), mShadowVerts.begin(), mShadowVerts.begin() + copyFloats);
        if (useNaviBatch) {
            NaviShadowCasterBatch batch;
            batch.firstFloat = firstFloat;
            batch.floatCount = copyFloats;
            batch.center[0] = center[0];
            batch.center[1] = center[1];
            batch.center[2] = center[2];
            mNaviShadowCasterBatches.push_back(batch);
        }
    }

    mShadowVerts.clear();
}

'''
    text = replace_between(
        text,
        flush_start,
        render_start,
        flush,
        "per-caster Navi routing",
    )

    render_function = text.find(render_start)
    if render_function < 0:
        raise RuntimeError("per-caster Navi resolve: render function not found")

    compact_candidates = (
        "    // Resolve compact dynamic actors separately",
        "    // Compact actors outside Navi's small influence radius",
        "    // Compact actors outside Navi's influence retain the stable environmental direction",
    )
    compact_start_index = -1
    for candidate in compact_candidates:
        compact_start_index = text.find(candidate, render_function)
        if compact_start_index >= 0:
            break
    if compact_start_index < 0:
        raise RuntimeError("per-caster Navi resolve: compact resolve marker not found")

    compact_end_line = "    mLargeShadowCasterAccum.clear();"
    compact_end_index = text.find(compact_end_line, compact_start_index)
    if compact_end_index < 0:
        raise RuntimeError("per-caster Navi resolve: large-caster clear not found")
    compact_end_index += len(compact_end_line)

    compact_resolve = r'''    // Compact actors outside Navi's influence retain the stable environmental direction.
    // No local fill is supplied, so distant actors never brighten merely because Navi exists elsewhere.
    const float noLocalFill[4] = { 0.0f, 0.0f, 0.0f, 0.0f };
    if (mShadowCasterAccum.size() >= 9) {
        const size_t actorVertexCount = mShadowCasterAccum.size() / 3;
        mRapi->RenderDynamicShadowMap(
            mShadowCasterAccum.data(), actorVertexCount, effectiveCamera, mDynamicShadowLightDir,
            mDynamicShadowAnchor, noLocalFill, kDynamicShadowMapResolution,
            std::clamp(mToonShadowAlpha, 0.0f, 1.0f), kDynamicShadowMapBias, kDynamicShadowMapPcfRadius);
    }

    // Each Navi-affected compact caster is resolved independently. Using one average centre for Link and another
    // actor made both shadows point from an imaginary light position and stretched the combined silhouette.
    for (const NaviShadowCasterBatch& batch : mNaviShadowCasterBatches) {
        if (batch.floatCount < 9 || batch.firstFloat + batch.floatCount > mNaviShadowCasterAccum.size() ||
            mDynamicShadowLocalLight[3] <= 0.0f) {
            continue;
        }

        float naviLightDirection[3] = {
            mDynamicShadowLocalLight[0] - batch.center[0],
            mDynamicShadowLocalLight[1] - batch.center[1],
            mDynamicShadowLocalLight[2] - batch.center[2],
        };
        float directionLengthSquared = naviLightDirection[0] * naviLightDirection[0] +
                                       naviLightDirection[1] * naviLightDirection[1] +
                                       naviLightDirection[2] * naviLightDirection[2];
        if (directionLengthSquared > 0.000001f) {
            const float inverseLength = 1.0f / std::sqrt(directionLengthSquared);
            naviLightDirection[0] *= inverseLength;
            naviLightDirection[1] *= inverseLength;
            naviLightDirection[2] *= inverseLength;

            // The backend approximates the point light with one directional projection per actor. A stronger
            // minimum elevation prevents a low side-positioned Navi from creating an excessively long silhouette.
            constexpr float kMinimumNaviElevation = 0.45f;
            if (naviLightDirection[1] < kMinimumNaviElevation) {
                const float horizontalLength = std::sqrt(naviLightDirection[0] * naviLightDirection[0] +
                                                         naviLightDirection[2] * naviLightDirection[2]);
                const float horizontalTarget = std::sqrt(1.0f - kMinimumNaviElevation * kMinimumNaviElevation);
                if (horizontalLength > 0.00001f) {
                    const float horizontalScale = horizontalTarget / horizontalLength;
                    naviLightDirection[0] *= horizontalScale;
                    naviLightDirection[2] *= horizontalScale;
                } else {
                    naviLightDirection[0] = horizontalTarget;
                    naviLightDirection[2] = 0.0f;
                }
                naviLightDirection[1] = kMinimumNaviElevation;
            }
        } else {
            naviLightDirection[0] = mDynamicShadowLightDir[0];
            naviLightDirection[1] = mDynamicShadowLightDir[1];
            naviLightDirection[2] = mDynamicShadowLightDir[2];
        }

        const size_t actorVertexCount = batch.floatCount / 3;
        mRapi->RenderDynamicShadowMap(
            mNaviShadowCasterAccum.data() + batch.firstFloat, actorVertexCount, effectiveCamera, naviLightDirection,
            batch.center, noLocalFill, kDynamicShadowMapResolution,
            std::clamp(mToonShadowAlpha, 0.0f, 1.0f), kDynamicShadowMapBias, kDynamicShadowMapPcfRadius);
    }

    mShadowCasterAccum.clear();
    mNaviShadowCasterAccum.clear();
    mNaviShadowCasterBatches.clear();
    mLargeShadowCasterAccum.clear();'''
    text = text[:compact_start_index] + compact_resolve + text[compact_end_index:]

    path.write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    patch_interpreter_header(root)
    patch_interpreter(root)
    print(
        "Rendered each compact Navi-affected caster with its own light direction and capped the extra passes."
    )


if __name__ == "__main__":
    main()
