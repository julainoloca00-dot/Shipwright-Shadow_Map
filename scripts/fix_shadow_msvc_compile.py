from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


root = Path(__file__).resolve().parents[1]

# -------------------------------------------------------------------------------------------------
# MSVC fixes for the generated libultraship shadow-cache code.
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
# A point light should soften a large environmental shadow, not erase a circular hole in the whole map.
# Strong removal for nearby small actor shadows is handled per caster below; this resolve-side fill is the
# deliberately weaker layer used by large/static world shadows. Patch both normal and MSAA resolve shaders.
# -------------------------------------------------------------------------------------------------
backend_path = root / "libultraship/src/fast/backends/gfx_direct3d11_shadow_map.inc"
backend_text = backend_path.read_text(encoding="utf-8")
old_world_fill = "const float localShadowScale = 1.0 - localFill * 0.85;"
new_world_fill = "const float localShadowScale = 1.0 - localFill * 0.35;"
world_fill_count = backend_text.count(old_world_fill)
if world_fill_count != 2:
    raise RuntimeError(f"Navi world-shadow fill strength: expected two shader matches, found {world_fill_count}")
backend_text = backend_text.replace(old_world_fill, new_world_fill)
backend_path.write_text(backend_text, encoding="utf-8", newline="\n")

# -------------------------------------------------------------------------------------------------
# Re-enable Navi as a LOCAL shadow fill while keeping her excluded as a shadow caster and keeping the
# stable sun/moon direction. Individual actor shadows near Navi are reduced much more strongly than the
# cached environment, which gives small props/characters a believable local-light response without making
# large tree/building shadows disappear as one giant circular patch.
# -------------------------------------------------------------------------------------------------
navi_policy_path = root / "soh/soh/Enhancements/Graphics/ZZNaviShadowLightFix.cpp"
navi_policy_text = navi_policy_path.read_text(encoding="utf-8")

if "kNaviShadowFillRadius" not in navi_policy_text:
    navi_policy_text = replace_once(
        navi_policy_text,
        "constexpr float kDirectionBlend = 0.06f;",
        '''constexpr float kDirectionBlend = 0.06f;
constexpr float kNaviShadowFillRadius = 320.0f;
constexpr float kNaviSmallCasterSuppression = 0.85f;
constexpr float kNaviLargeCasterSuppression = 0.25f;''',
        "Navi shadow interaction constants",
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

    // Navi remains excluded from caster capture and never changes the stable sun/moon direction. She only
    // contributes a local radial fill whose world-shadow strength is deliberately limited in the DX11 resolve.
    interpreter->SetDynamicShadowCaptureState(true, sStableShadowDirection, anchor, localShadowLight);''',
    "restore Navi local shadow fill",
)

navi_policy_text = replace_once(
    navi_policy_text,
    '''    const float deltaTime = (R_UPDATE_RATE > 0 ? R_UPDATE_RATE : 3) / 60.0f;
    const float targetScale = insideStableRange && hasFloor && !onWall ? 1.0f : 0.0f;
    state.scale = SmoothDamp(state.scale, targetScale, &state.scaleVelocity, kShadowFadeTime, deltaTime);''',
    '''    const float deltaTime = (R_UPDATE_RATE > 0 ? R_UPDATE_RATE : 3) / 60.0f;
    float targetScale = insideStableRange && hasFloor && !onWall ? 1.0f : 0.0f;

    // Local point-light response for caster shadows. Small actors/props close to Navi lose most of their
    // directional shadow; large casters such as tree canopies keep most of theirs and are merely softened.
    // The smooth radial curve prevents popping as Navi orbits Link or the actor crosses the light boundary.
    if (targetScale > 0.0f &&
        CVarGetInteger(CVAR_ENHANCEMENT("Graphics.ToonLighting.UseNaviLight"), 1) != 0) {
        Player* shadowPlayer = GET_PLAYER(play);
        Actor* naviActor = shadowPlayer != nullptr ? shadowPlayer->naviActor : nullptr;
        if (IsNavi(naviActor)) {
            const float naviDx = actor->world.pos.x - naviActor->world.pos.x;
            const float naviDy = actor->world.pos.y - naviActor->world.pos.y;
            const float naviDz = actor->world.pos.z - naviActor->world.pos.z;
            const float naviDistance = std::sqrt(naviDx * naviDx + naviDy * naviDy + naviDz * naviDz);
            float naviInfluence = std::clamp(1.0f - naviDistance / kNaviShadowFillRadius, 0.0f, 1.0f);
            naviInfluence = naviInfluence * naviInfluence * (3.0f - 2.0f * naviInfluence);

            const bool largeCaster = actor->id == ACTOR_EN_WOOD02 || actor->category == ACTORCAT_BG;
            const float maximumSuppression =
                largeCaster ? kNaviLargeCasterSuppression : kNaviSmallCasterSuppression;
            targetScale *= 1.0f - naviInfluence * maximumSuppression;
        }
    }

    state.scale = SmoothDamp(state.scale, targetScale, &state.scaleVelocity, kShadowFadeTime, deltaTime);''',
    "Navi per-actor shadow response",
)

navi_policy_path.write_text(navi_policy_text, encoding="utf-8", newline="\n")

print(
    "Applied MSVC fixes, restored Navi lightcasting, and balanced local shadow interaction for small and large casters."
)
