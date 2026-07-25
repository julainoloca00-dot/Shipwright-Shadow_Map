from __future__ import annotations

import re
from pathlib import Path


def replace_regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.DOTALL)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one structural match, found {count}")
    return updated


def patch_interpreter_header(root: Path) -> None:
    path = root / "libultraship/include/fast/interpreter.h"
    text = path.read_text(encoding="utf-8")

    if "mNaviShadowCasterAccum" not in text:
        text = replace_regex_once(
            text,
            r"(\s*std::vector<float> mShadowCasterAccum;\n)",
            r'''\1    // Compact casters close enough to a visible Navi. These receive a dedicated shadow-map pass
    // whose projection direction points toward Navi instead of using the sun/moon direction.
    std::vector<float> mNaviShadowCasterAccum;
    float mNaviShadowCasterCenterSum[3] = { 0.0f, 0.0f, 0.0f };
    uint32_t mNaviShadowCasterCount = 0;
''',
            "Navi-directed compact caster state",
        )

    if "mNaviShadowCasterAccum.clear();" not in text:
        text = replace_regex_once(
            text,
            r"(\s*mShadowCasterAccum\.clear\(\);\n)(\s*mLargeShadowCasterAccum\.clear\(\);)",
            r'''\1            mNaviShadowCasterAccum.clear();
            mNaviShadowCasterCenterSum[0] = 0.0f;
            mNaviShadowCasterCenterSum[1] = 0.0f;
            mNaviShadowCasterCenterSum[2] = 0.0f;
            mNaviShadowCasterCount = 0;
\2''',
            "Navi-directed caster disable cleanup",
        )

    path.write_text(text, encoding="utf-8", newline="\n")


def patch_interpreter(root: Path) -> None:
    path = root / "libultraship/src/fast/interpreter.cpp"
    text = path.read_text(encoding="utf-8")

    flush = r'''void Interpreter::FlushToonShadow() {
    const float coreAlpha = std::clamp(mToonShadowAlpha, 0.0f, 1.0f);
    if (mShadowVerts.size() < 9 || coreAlpha <= 0.0f || !mRdp->toon_shadow) {
        mShadowVerts.clear();
        return;
    }

    // Classify the complete current object by its world-space bounds. Large actors keep the environment
    // direction. A compact actor close to a visible Navi is routed to a separate pass so the fairy becomes
    // the actual shadow-light direction instead of merely bleaching the existing sun/moon shadow.
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
    const float actorHalfExtent = std::max({ (maximum[0] - minimum[0]) * 0.5f,
                                             (maximum[1] - minimum[1]) * 0.5f,
                                             (maximum[2] - minimum[2]) * 0.5f });
    const float effectiveNaviRadius = naviRadius + std::min(actorHalfExtent, 64.0f);
    const bool naviDirectedCaster = !largeCaster && naviRadius > 0.0f &&
                                    naviDx * naviDx + naviDy * naviDy + naviDz * naviDz <=
                                        effectiveNaviRadius * effectiveNaviRadius;

    std::vector<float>& destination = largeCaster
                                          ? mLargeShadowCasterAccum
                                          : (naviDirectedCaster ? mNaviShadowCasterAccum : mShadowCasterAccum);

    // Keep every layer independently bounded. Dropping the newest object is preferable to an unbounded frame spike.
    const size_t available = destination.size() < kShadowAccumBudgetFloats
                                 ? kShadowAccumBudgetFloats - destination.size()
                                 : 0;
    const size_t copyFloats = std::min(mShadowVerts.size(), available - (available % 9));
    if (copyFloats >= 9) {
        destination.insert(destination.end(), mShadowVerts.begin(), mShadowVerts.begin() + copyFloats);
        if (naviDirectedCaster) {
            mNaviShadowCasterCenterSum[0] += center[0];
            mNaviShadowCasterCenterSum[1] += center[1];
            mNaviShadowCasterCenterSum[2] += center[2];
            ++mNaviShadowCasterCount;
        }
    }

    mShadowVerts.clear();
}'''

    text = replace_regex_once(
        text,
        r"void Interpreter::FlushToonShadow\(\) \{.*?\n\}\n\nvoid Interpreter::RenderShadowVolumes\(\)",
        flush + "\n\nvoid Interpreter::RenderShadowVolumes()",
        "compact caster routing by Navi distance",
    )

    compact_resolve = r'''    // Compact actors outside Navi's small influence radius retain the stable environmental direction.
    // No local-fill radius is supplied: distant actors must not brighten merely because Navi exists elsewhere.
    const float noLocalFill[4] = { 0.0f, 0.0f, 0.0f, 0.0f };
    if (mShadowCasterAccum.size() >= 9) {
        const size_t actorVertexCount = mShadowCasterAccum.size() / 3;
        mRapi->RenderDynamicShadowMap(
            mShadowCasterAccum.data(), actorVertexCount, effectiveCamera, mDynamicShadowLightDir,
            mDynamicShadowAnchor, noLocalFill, kDynamicShadowMapResolution,
            std::clamp(mToonShadowAlpha, 0.0f, 1.0f), kDynamicShadowMapBias, kDynamicShadowMapPcfRadius);
    }

    // For compact actors close to a visible Navi, the fairy is the primary light direction. This creates a real
    // cast-away shadow rather than fading the sun/moon shadow. The average caster centre keeps one inexpensive
    // directional pass coherent for Link and nearby small actors without requiring one shadow map per actor.
    if (mNaviShadowCasterAccum.size() >= 9 && mNaviShadowCasterCount > 0 && mDynamicShadowLocalLight[3] > 0.0f) {
        const float inverseCasterCount = 1.0f / static_cast<float>(mNaviShadowCasterCount);
        const float casterCenter[3] = {
            mNaviShadowCasterCenterSum[0] * inverseCasterCount,
            mNaviShadowCasterCenterSum[1] * inverseCasterCount,
            mNaviShadowCasterCenterSum[2] * inverseCasterCount,
        };
        float naviLightDirection[3] = {
            mDynamicShadowLocalLight[0] - casterCenter[0],
            mDynamicShadowLocalLight[1] - casterCenter[1],
            mDynamicShadowLocalLight[2] - casterCenter[2],
        };
        float naviDirectionLengthSquared = naviLightDirection[0] * naviLightDirection[0] +
                                           naviLightDirection[1] * naviLightDirection[1] +
                                           naviLightDirection[2] * naviLightDirection[2];
        if (naviDirectionLengthSquared > 0.000001f) {
            const float inverseLength = 1.0f / std::sqrt(naviDirectionLengthSquared);
            naviLightDirection[0] *= inverseLength;
            naviLightDirection[1] *= inverseLength;
            naviLightDirection[2] *= inverseLength;

            // The backend is directional rather than a six-face point-shadow map. Keep a minimum upward component
            // so a horizontally moving fairy cannot create an infinitely long projected silhouette.
            constexpr float kMinimumNaviElevation = 0.25f;
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

        const size_t naviActorVertexCount = mNaviShadowCasterAccum.size() / 3;
        mRapi->RenderDynamicShadowMap(
            mNaviShadowCasterAccum.data(), naviActorVertexCount, effectiveCamera, naviLightDirection,
            mDynamicShadowAnchor, noLocalFill, kDynamicShadowMapResolution,
            std::clamp(mToonShadowAlpha, 0.0f, 1.0f), kDynamicShadowMapBias, kDynamicShadowMapPcfRadius);
    }

    mShadowCasterAccum.clear();
    mNaviShadowCasterAccum.clear();
    mLargeShadowCasterAccum.clear();
    mNaviShadowCasterCenterSum[0] = 0.0f;
    mNaviShadowCasterCenterSum[1] = 0.0f;
    mNaviShadowCasterCenterSum[2] = 0.0f;
    mNaviShadowCasterCount = 0;'''

    text = replace_regex_once(
        text,
        r"    // Resolve compact dynamic actors separately.*?\n    mShadowCasterAccum\.clear\(\);\n    mLargeShadowCasterAccum\.clear\(\);",
        compact_resolve,
        "Navi-directed compact shadow resolve",
    )

    path.write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    patch_interpreter_header(root)
    patch_interpreter(root)
    print(
        "Routed compact nearby actors to a Navi-directed shadow pass; world, large and distant casters keep environment direction."
    )


if __name__ == "__main__":
    main()
