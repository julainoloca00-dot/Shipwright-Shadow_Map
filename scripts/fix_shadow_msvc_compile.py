from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


root = Path(__file__).resolve().parents[1]

# -------------------------------------------------------------------------------------------------
# Extend the generated interpreter state with a separate accumulator for physically large actors.
# Large actor shadows (trees, bosses, oversized props) use the weak local-light response together with
# the static world; compact actors and props use the strong Navi response.
# -------------------------------------------------------------------------------------------------
header_path = root / "libultraship/include/fast/interpreter.h"
header_text = header_path.read_text(encoding="utf-8")

old_actor_accumulator = '''    std::vector<float> mShadowCasterAccum;
    // Visible opaque environment triangles captured before the actor pass.'''
new_actor_accumulator = '''    std::vector<float> mShadowCasterAccum;
    // Dynamic casters whose captured world-space bounds are too large for a point light to erase as one
    // compact shadow. They are resolved with the weaker environment-style local fill.
    std::vector<float> mLargeShadowCasterAccum;
    // Visible opaque environment triangles captured before the actor pass.'''
header_text = replace_once(
    header_text,
    old_actor_accumulator,
    new_actor_accumulator,
    "large-caster accumulator declaration",
)

old_disable_cleanup = '''            mEnvironmentShadowTriangleHashes.clear();
            mEnvironmentShadowCacheAnchorValid = false;
            mShadowCasterAccum.clear();
            mShadowVerts.clear();'''
new_disable_cleanup = '''            mEnvironmentShadowTriangleHashes.clear();
            mEnvironmentShadowCacheAnchorValid = false;
            mShadowCasterAccum.clear();
            mLargeShadowCasterAccum.clear();
            mShadowVerts.clear();'''
header_text = replace_once(
    header_text,
    old_disable_cleanup,
    new_disable_cleanup,
    "large-caster disable cleanup",
)
header_path.write_text(header_text, encoding="utf-8", newline="\n")

# -------------------------------------------------------------------------------------------------
# MSVC fixes, caster-size classification and layered shadow resolve for generated libultraship code.
# -------------------------------------------------------------------------------------------------
interpreter_path = root / "libultraship/src/fast/interpreter.cpp"
interpreter_text = interpreter_path.read_text(encoding="utf-8")

old_actor_boundary = '''    // The first actor marker closes room-only environment capture. The actual resolve may occur after all
    // opaque actors so Link can receive shadows, but actor geometry must never leak into the static-world cache.
    mCaptureEnvironmentShadow = false;

    // Sentinel (gSPToonShadowFlush):'''
new_actor_boundary = '''    // The first actor marker closes room-only environment capture. The actual resolve may occur after all
    // opaque actors so Link can receive shadows, but actor geometry must never leak into the static-world cache.
    gfx->mCaptureEnvironmentShadow = false;

    // Sentinel (gSPToonShadowFlush):'''
interpreter_text = replace_once(
    interpreter_text,
    old_actor_boundary,
    new_actor_boundary,
    "actor-boundary Interpreter access",
)

# Use the standard C++ overload instead of relying on the global C spelling exposed by a specific CRT.
interpreter_text = interpreter_text.replace("llroundf(", "std::llround(")

old_flush = '''void Interpreter::FlushToonShadow() {
    const float coreAlpha = std::clamp(mToonShadowAlpha, 0.0f, 1.0f);
    if (mShadowVerts.size() < 9 || coreAlpha <= 0.0f || !mRdp->toon_shadow) {
        mShadowVerts.clear();
        return;
    }

    // Keep the buffer bounded. The data is three floats per vertex and is consumed once per frame.
    // Drop newest casters once the budget is full, matching the old volume accumulator's behavior.
    const size_t available =
        mShadowCasterAccum.size() < kShadowAccumBudgetFloats ? kShadowAccumBudgetFloats - mShadowCasterAccum.size() : 0;
    const size_t copyFloats = std::min(mShadowVerts.size(), available - (available % 9));
    if (copyFloats >= 9) {
        mShadowCasterAccum.insert(mShadowCasterAccum.end(), mShadowVerts.begin(), mShadowVerts.begin() + copyFloats);
    }

    mShadowVerts.clear();
}'''
new_flush = '''void Interpreter::FlushToonShadow() {
    const float coreAlpha = std::clamp(mToonShadowAlpha, 0.0f, 1.0f);
    if (mShadowVerts.size() < 9 || coreAlpha <= 0.0f || !mRdp->toon_shadow) {
        mShadowVerts.clear();
        return;
    }

    // Classify the complete current object by its world-space bounds. Compact casters can have most of their
    // directional shadow cleared by a nearby point light. Trees, bosses and oversized props keep a weaker,
    // world-like shadow response so Navi never punches an implausibly large circular hole through them.
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
    std::vector<float>& destination = largeCaster ? mLargeShadowCasterAccum : mShadowCasterAccum;

    // Keep each frame buffer bounded. Drop newest casters once its budget is full instead of growing without limit.
    const size_t available = destination.size() < kShadowAccumBudgetFloats
                                 ? kShadowAccumBudgetFloats - destination.size()
                                 : 0;
    const size_t copyFloats = std::min(mShadowVerts.size(), available - (available % 9));
    if (copyFloats >= 9) {
        destination.insert(destination.end(), mShadowVerts.begin(), mShadowVerts.begin() + copyFloats);
    }

    mShadowVerts.clear();
}'''
interpreter_text = replace_once(
    interpreter_text,
    old_flush,
    new_flush,
    "classify compact and large actor casters",
)

old_combined_resolve = '''    // Temporarily append dynamic actor casters to the stable world cache for this resolve only.
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
    mShadowCasterAccum.clear();'''

new_layered_resolve = '''    // Fast3D applies its widescreen correction to clip X after P_matrix. Pass the exact effective matrix so
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

    // Large dynamic casters join the world cache only for this frame's weak-fill resolve. They are not persisted.
    const size_t stableEnvironmentFloats = mEnvironmentShadowCasterCache.size();
    const size_t availableForLargeCasters = stableEnvironmentFloats < kShadowAccumBudgetFloats
                                                ? kShadowAccumBudgetFloats - stableEnvironmentFloats
                                                : 0;
    const size_t largeCasterFloats =
        std::min(mLargeShadowCasterAccum.size(), availableForLargeCasters - (availableForLargeCasters % 9));
    if (largeCasterFloats >= 9) {
        mEnvironmentShadowCasterCache.insert(mEnvironmentShadowCasterCache.end(),
                                             mLargeShadowCasterAccum.begin(),
                                             mLargeShadowCasterAccum.begin() + largeCasterFloats);
    }

    // Resolve static/world and physically large casters first. A negative radius selects the weaker local-fill
    // strength in the DX11 shader, so their shadows are softened rather than erased as a circular hole.
    if (mEnvironmentShadowCasterCache.size() >= 9) {
        float environmentLocalLight[4] = { mDynamicShadowLocalLight[0], mDynamicShadowLocalLight[1],
                                           mDynamicShadowLocalLight[2], mDynamicShadowLocalLight[3] };
        if (environmentLocalLight[3] > 0.0f) {
            environmentLocalLight[3] = -environmentLocalLight[3];
        }
        const size_t environmentVertexCount = mEnvironmentShadowCasterCache.size() / 3;
        mRapi->RenderDynamicShadowMap(
            mEnvironmentShadowCasterCache.data(), environmentVertexCount, effectiveCamera, mDynamicShadowLightDir,
            mDynamicShadowAnchor, environmentLocalLight, kDynamicShadowMapResolution,
            std::clamp(mToonShadowAlpha, 0.0f, 1.0f), kDynamicShadowMapBias, kDynamicShadowMapPcfRadius);
    }
    mEnvironmentShadowCasterCache.resize(stableEnvironmentFloats);

    // Resolve compact dynamic actors separately with the positive radius/strong local fill. Navi can heavily
    // soften Link, NPC, enemy and small-prop shadows without weakening trees, bosses or the whole world.
    if (mShadowCasterAccum.size() >= 9) {
        const size_t actorVertexCount = mShadowCasterAccum.size() / 3;
        mRapi->RenderDynamicShadowMap(
            mShadowCasterAccum.data(), actorVertexCount, effectiveCamera, mDynamicShadowLightDir,
            mDynamicShadowAnchor, mDynamicShadowLocalLight, kDynamicShadowMapResolution,
            std::clamp(mToonShadowAlpha, 0.0f, 1.0f), kDynamicShadowMapBias, kDynamicShadowMapPcfRadius);
    }

    mShadowCasterAccum.clear();
    mLargeShadowCasterAccum.clear();'''
interpreter_text = replace_once(
    interpreter_text,
    old_combined_resolve,
    new_layered_resolve,
    "split environment, large-caster and compact-actor shadow response",
)
interpreter_path.write_text(interpreter_text, encoding="utf-8", newline="\n")

# -------------------------------------------------------------------------------------------------
# Restore the actual world-light pool. The previous stability pass removed the whole draw hook, which
# also disabled the user-facing "Enable Navi Lightcasting" option. The hook itself already owns its CVar
# checks, so restoring the call does not force the effect on when the option is disabled.
# -------------------------------------------------------------------------------------------------
actor_draw_path = root / "soh/src/code/z_actor.c"
actor_draw_text = actor_draw_path.read_text(encoding="utf-8")
removed_world_light_call = '''    // Point lights, including Navi, continue to affect actor toon lighting through ToonLighting.cpp.
    // Do not draw the old world-space light pool: Navi's pool was the large circular ball on the ground.'''
world_light_call = "    GameInteractor_ExecuteOnPlayDrawWorldLights(play);"
if removed_world_light_call in actor_draw_text:
    actor_draw_text = replace_once(
        actor_draw_text,
        removed_world_light_call,
        world_light_call,
        "restore Navi/world lightcasting draw hook",
    )
elif world_light_call not in actor_draw_text:
    raise RuntimeError("restore Navi/world lightcasting draw hook: neither removed marker nor live call was found")
actor_draw_path.write_text(actor_draw_text, encoding="utf-8", newline="\n")

# -------------------------------------------------------------------------------------------------
# Signed local-light radius selects the fill strength per shadow layer:
#   positive radius = strong compact-actor clearing (85% at the centre),
#   negative radius = weak world/large-caster softening (35% at the centre).
# Patch both normal and MSAA resolve shaders.
# -------------------------------------------------------------------------------------------------
backend_path = root / "libultraship/src/fast/backends/gfx_direct3d11_shadow_map.inc"
backend_text = backend_path.read_text(encoding="utf-8")
old_local_fill = '''    float localFill = 0.0;
    if (localLight.w > 0.0) {
        localFill = saturate(1.0 - length(world.xyz - localLight.xyz) / localLight.w);
        localFill = localFill * localFill * (3.0 - 2.0 * localFill);
    }
    const float localShadowScale = 1.0 - localFill * 0.85;'''
new_local_fill = '''    float localFill = 0.0;
    const float localRadius = abs(localLight.w);
    if (localRadius > 0.0) {
        localFill = saturate(1.0 - length(world.xyz - localLight.xyz) / localRadius);
        localFill = localFill * localFill * (3.0 - 2.0 * localFill);
    }
    const float localFillStrength = localLight.w < 0.0 ? 0.35 : 0.85;
    const float localShadowScale = 1.0 - localFill * localFillStrength;'''
local_fill_count = backend_text.count(old_local_fill)
if local_fill_count != 2:
    raise RuntimeError(f"layered Navi shadow fill: expected two shader matches, found {local_fill_count}")
backend_text = backend_text.replace(old_local_fill, new_local_fill)
backend_path.write_text(backend_text, encoding="utf-8", newline="\n")

# -------------------------------------------------------------------------------------------------
# Re-enable Navi as a LOCAL shadow fill while keeping her excluded as a caster and keeping the stable
# sun/moon direction. The signed-radius distinction is applied only when each layer is resolved above.
# -------------------------------------------------------------------------------------------------
navi_policy_path = root / "soh/soh/Enhancements/Graphics/ZZNaviShadowLightFix.cpp"
navi_policy_text = navi_policy_path.read_text(encoding="utf-8")

if "kNaviShadowFillRadius" not in navi_policy_text:
    navi_policy_text = replace_once(
        navi_policy_text,
        "constexpr float kDirectionBlend = 0.06f;",
        '''constexpr float kDirectionBlend = 0.06f;
constexpr float kNaviShadowFillRadius = 320.0f;''',
        "Navi shadow interaction radius",
    )

navi_policy_text = replace_once(
    navi_policy_text,
    '''    float anchor[3] = { 0.0f, 0.0f, 0.0f };
    const float disabledLocalLight[4] = { 0.0f, 0.0f, 0.0f, 0.0f };''',
    '''    float anchor[3] = { 0.0f, 0.0f, 0.0f };
    float localShadowLight[4] = { 0.0f, 0.0f, 0.0f, 0.0f };''',
    "Navi local shadow-light storage",
)
navi_policy_text = navi_policy_text.replace("disabledLocalLight", "localShadowLight")

navi_policy_text = replace_once(
    navi_policy_text,
    '''            anchor[0] = player->actor.world.pos.x;
            anchor[1] = player->actor.world.pos.y;
            anchor[2] = player->actor.world.pos.z;
        }
    }

    // Radius zero is intentional: Navi may illuminate toon shading, but she never modifies the world shadow map.
    interpreter->SetDynamicShadowCaptureState(true, sStableShadowDirection, anchor, localShadowLight);''',
    '''            anchor[0] = player->actor.world.pos.x;
            anchor[1] = player->actor.world.pos.y;
            anchor[2] = player->actor.world.pos.z;

            Actor* naviActor = player->naviActor;
            const bool naviLightcastingEnabled =
                CVarGetInteger(CVAR_ENHANCEMENT("Graphics.ToonLighting.UseNaviLight"), 1) != 0;
            if (naviLightcastingEnabled && naviActor != nullptr && naviActor->id == ACTOR_EN_ELF &&
                naviActor->params == FAIRY_NAVI) {
                localShadowLight[0] = naviActor->world.pos.x;
                localShadowLight[1] = naviActor->world.pos.y;
                localShadowLight[2] = naviActor->world.pos.z;
                localShadowLight[3] = kNaviShadowFillRadius;
            }
        }
    }

    // Navi remains excluded from caster capture and never changes the stable sun/moon direction. Her local
    // radius is consumed separately by the weak world/large pass and the strong compact-actor pass.
    interpreter->SetDynamicShadowCaptureState(true, sStableShadowDirection, anchor, localShadowLight);''',
    "restore Navi local shadow fill",
)

navi_policy_path.write_text(navi_policy_text, encoding="utf-8", newline="\n")

print(
    "Applied MSVC fixes, restored Navi lightcasting, and split weak world/large and strong compact shadow response."
)
