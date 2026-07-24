// Corrective world-shadow light policy for Navi.
// Revision: receiver-to-light direction, no radial shadow halo, and no fairy caster geometry.
//
// ToonLighting.cpp originally forwards Navi as a radial shadow-opacity mask. That creates a circular
// light/dark boundary and can visually resemble a shadow emitted by the fairy. This module runs after
// ToonLighting.cpp's hooks and replaces that state with a directional approximation of a point light:
// the vector is computed from Link toward Navi, shadows project away from the fairy, and their opacity is
// reduced mainly in dark environments. It also disarms both actor streams before Navi draws so the light
// source itself can never be captured as shadow-casting geometry.

#include <algorithm>
#include <cmath>
#include <memory>

#include <fast/Fast3dWindow.h>
#include <fast/interpreter.h>
#include <ship/Context.h>

#include "soh/Enhancements/game-interactor/GameInteractor.h"
#include "soh/ShipInit.hpp"
#include "soh/cvar_prefixes.h"
#include "soh/frame_interpolation.h"

extern "C" {
#include "functions.h"
#include "macros.h"
#include "variables.h"
#include "z64.h"
#include "overlays/actors/ovl_En_Elf/z_en_elf.h"

extern PlayState* gPlayState;
}

namespace {

std::shared_ptr<Fast::Interpreter> GetShadowInterpreter() {
    auto window = std::dynamic_pointer_cast<Fast::Fast3dWindow>(Ship::Context::GetInstance()->GetWindow());
    if (window == nullptr) {
        return nullptr;
    }
    return window->GetInterpreterWeak().lock();
}

void NormalizeOrDefault(float direction[3]) {
    const float lengthSquared = direction[0] * direction[0] + direction[1] * direction[1] +
                                direction[2] * direction[2];
    if (lengthSquared <= 0.000001f) {
        direction[0] = 0.30f;
        direction[1] = 1.00f;
        direction[2] = 0.20f;
        return;
    }

    const float inverseLength = 1.0f / std::sqrt(lengthSquared);
    direction[0] *= inverseLength;
    direction[1] *= inverseLength;
    direction[2] *= inverseLength;
}

void ReadEnvironmentKey(PlayState* play, float direction[3], float* luminance) {
    LightInfo* sun = &play->envCtx.dirLight1;
    LightInfo* moon = &play->envCtx.dirLight2;
    const int sunLuminance = sun->params.dir.color[0] + sun->params.dir.color[1] + sun->params.dir.color[2];
    const int moonLuminance = moon->params.dir.color[0] + moon->params.dir.color[1] + moon->params.dir.color[2];
    LightInfo* environment = moonLuminance > sunLuminance ? moon : sun;

    direction[0] = environment->params.dir.x;
    direction[1] = environment->params.dir.y;
    direction[2] = environment->params.dir.z;
    NormalizeOrDefault(direction);

    *luminance = std::clamp((environment->params.dir.color[0] + environment->params.dir.color[1] +
                             environment->params.dir.color[2]) /
                                (3.0f * 255.0f),
                            0.0f, 1.0f);
}

void ApplyNaviShadowLightFix() {
    auto interpreter = GetShadowInterpreter();
    if (interpreter == nullptr) {
        return;
    }

    const bool shadowsEnabled =
        CVarGetInteger(CVAR_ENHANCEMENT("Graphics.WorldShadows.Enabled"), 0) != 0;
    if (!shadowsEnabled) {
        return;
    }

    float shadowDirection[3] = { 0.30f, 1.00f, 0.20f };
    float shadowAnchor[3] = { 0.0f, 0.0f, 0.0f };
    float environmentLuminance = 1.0f;
    float naviInfluence = 0.0f;

    if (gPlayState != nullptr) {
        ReadEnvironmentKey(gPlayState, shadowDirection, &environmentLuminance);

        Player* player = GET_PLAYER(gPlayState);
        if (player != nullptr) {
            shadowAnchor[0] = player->actor.world.pos.x;
            shadowAnchor[1] = player->actor.world.pos.y;
            shadowAnchor[2] = player->actor.world.pos.z;

            Actor* naviActor = player->naviActor;
            const bool useNaviLight =
                CVarGetInteger(CVAR_ENHANCEMENT("Graphics.ToonLighting.UseNaviLight"), 1) != 0;
            if (useNaviLight && naviActor != nullptr && naviActor->id == ACTOR_EN_ELF &&
                naviActor->params == FAIRY_NAVI) {
                // Direction expected by the shadow map is receiver -> light. Using the opposite vector
                // makes the shadow lean toward Navi and visually reads as darkness emitted by the fairy.
                float naviDirection[3] = {
                    naviActor->world.pos.x - shadowAnchor[0],
                    naviActor->world.pos.y - shadowAnchor[1],
                    naviActor->world.pos.z - shadowAnchor[2],
                };
                const float naviLengthSquared = naviDirection[0] * naviDirection[0] +
                                                naviDirection[1] * naviDirection[1] +
                                                naviDirection[2] * naviDirection[2];
                if (naviLengthSquared > 0.000001f) {
                    NormalizeOrDefault(naviDirection);

                    // Navi becomes the dominant key mainly when the environmental light is weak.
                    // Daylight keeps 10% influence; a fully dark scene reaches 90%.
                    const float darkness = 1.0f - environmentLuminance;
                    naviInfluence = 0.10f + darkness * 0.80f;

                    shadowDirection[0] += (naviDirection[0] - shadowDirection[0]) * naviInfluence;
                    shadowDirection[1] += (naviDirection[1] - shadowDirection[1]) * naviInfluence;
                    shadowDirection[2] += (naviDirection[2] - shadowDirection[2]) * naviInfluence;
                    NormalizeOrDefault(shadowDirection);
                }
            }
        }
    }

    // Radius zero disables the old radial opacity subtraction, removing the artificial ring around Navi.
    const float disabledRadialLight[4] = { 0.0f, 0.0f, 0.0f, 0.0f };
    interpreter->SetDynamicShadowCaptureState(true, shadowDirection, shadowAnchor, disabledRadialLight);

    // Reapply the normal shadow settings after ToonLighting.cpp, attenuating only the final environment
    // shadow strength. At maximum night influence, roughly half of the directional darkness remains.
    float opacity = CVarGetFloat(CVAR_ENHANCEMENT("Graphics.WorldShadows.Opacity"), 0.20f);
    opacity *= 1.0f - 0.55f * naviInfluence;

    const float length = CVarGetFloat(CVAR_ENHANCEMENT("Graphics.WorldShadows.Length"), 0.20f);
    const float slabDepth = CVarGetFloat(CVAR_ENHANCEMENT("Graphics.WorldShadows.SlabDepth"), 8.0f);
    const float slabRise = CVarGetFloat(CVAR_ENHANCEMENT("Graphics.WorldShadows.SlabRise"), 8.0f);
    const int edgeSoftness =
        CVarGetInteger(CVAR_ENHANCEMENT("Graphics.WorldShadows.EdgeSoftness"), 0);
    const bool showVolume = CVarGetInteger(CVAR_DEVELOPER_TOOLS("WorldShadows.ShowVolume"), 0) != 0;
    const float minElevation = 0.95f - std::clamp(length, 0.0f, 1.0f) * 0.85f;

    interpreter->SetToonShadowParams(opacity, minElevation, slabDepth, slabRise, edgeSoftness, showVolume);
}

void PreventNaviShadowCaster(void* actorPointer) {
    PlayState* play = gPlayState;
    Actor* actor = static_cast<Actor*>(actorPointer);
    if (play == nullptr || actor == nullptr || actor->id != ACTOR_EN_ELF || actor->params != FAIRY_NAVI ||
        CVarGetInteger(CVAR_ENHANCEMENT("Graphics.WorldShadows.Enabled"), 0) == 0) {
        return;
    }

    // ToonLighting.cpp may arm the generic actor caster before this later hook executes. Close both streams
    // again before En_Elf submits any geometry: the fairy core, glow cards, and translucent particles must
    // illuminate the scene but must never become occluders in the directional depth map.
    OPEN_DISPS(play->state.gfxCtx);
    gSPToonShadow(POLY_OPA_DISP++, 0, 0, 0, 0.0f);
    gSPToonShadow(POLY_XLU_DISP++, 0, 0, 0, 0.0f);
    CLOSE_DISPS(play->state.gfxCtx);
}

void RegisterNaviShadowLightFix() {
    COND_HOOK(OnGameFrameUpdate, true, ApplyNaviShadowLightFix);
    COND_HOOK(OnActorDraw, true, PreventNaviShadowCaster);
}

} // namespace

// The Z-prefixed filename sorts after ToonLighting.cpp in CMake's recursive glob, so these hooks are
// registered afterwards and intentionally become the final shadow-light policy for each frame/actor draw.
static RegisterShipInitFunc initNaviShadowLightFix(
    RegisterNaviShadowLightFix,
    { CVAR_ENHANCEMENT("Graphics.ToonLighting.UseNaviLight"),
      CVAR_ENHANCEMENT("Graphics.WorldShadows.Enabled") });
